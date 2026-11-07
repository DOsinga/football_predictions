"""Build a country-level dataset for national-team football strength.

The target is each team's average Elo rating over the previous ten years. The
predictors are public country data: GDP per head, population, migration, male
height, working hours, and World Cup history from this project.

Usage:
    python3 build_country_factors.py
    python3 build_country_factors.py --write data/country_factors.csv
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import time
import unicodedata
import warnings
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
from requests import HTTPError
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import r2_score
from sklearn.model_selection import KFold, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

sys.path.insert(0, str(Path(__file__).parent))
from fetch_elo import parse_team_history, team_url  # noqa: E402
from yaml_compat import safe_load  # noqa: E402


ROOT = Path(__file__).parent
CACHE = ROOT / "data" / "cache"
TEAM_URL = "https://www.eloratings.net/en.teams.tsv"
WORLD_BANK_COUNTRIES_URL = "https://api.worldbank.org/v2/country/all?format=json&per_page=400"
WORLD_BANK_INDICATORS = {
    "gdp_per_capita_usd": "NY.GDP.PCAP.KD",
    "gdp_per_capita_ppp": "NY.GDP.PCAP.PP.KD",
    "population": "SP.POP.TOTL",
    "foreign_born_pct": "SM.POP.TOTL.ZS",
}
OWID_HEIGHT_URL = "https://ourworldindata.org/grapher/average-height-of-men.csv?v=1&csvType=full&useColumnShortNames=false"
OWID_HOURS_URL = "https://ourworldindata.org/grapher/annual-working-hours-per-worker.csv?v=1&csvType=full&useColumnShortNames=false"
WIKIPEDIA_CATHOLIC_URL = "https://en.wikipedia.org/wiki/Catholic_Church_by_country"
HEADERS = {"User-Agent": "football-predictions-country-factors/0.1"}


# Elo mostly uses ISO-3166 alpha-2 codes, but football has home nations,
# historical teams, and a few old aliases. These are the places where names or
# codes differ from World Bank / OWID ISO3 codes.
ISO3_OVERRIDES = {
    "AN": "ANT",
    "BO": "BOL",
    "CI": "CIV",
    "CS": "SRB",
    "CD": "COD",
    "CG": "COG",
    "CZ": "CZE",
    "EC": "ECU",
    "EG": "EGY",
    "EN": "GBR",
    "GM": "GMB",
    "IR": "IRN",
    "KO": "XKX",
    "KR": "KOR",
    "KS": "XKX",
    "MK": "MKD",
    "NO": "NOR",
    "NM": "MKD",
    "RS": "SRB",
    "SC": "GBR",
    "SU": "RUS",
    "SX": "SXM",
    "TN": "TUN",
    "TR": "TUR",
    "TZ": "TZA",
    "WA": "GBR",
    "ZR": "COD",
}

NAME_OVERRIDES = {
    "Bahamas": "Bahamas, The",
    "Bolivia": "Bolivia",
    "Cape Verde": "Cabo Verde",
    "China": "China",
    "Congo": "Congo, Rep.",
    "Democratic Republic of the Congo": "Congo, Dem. Rep.",
    "Egypt": "Egypt, Arab Rep.",
    "Gambia": "Gambia, The",
    "Iran": "Iran, Islamic Rep.",
    "Ivory Coast": "Cote d'Ivoire",
    "Kyrgyzstan": "Kyrgyz Republic",
    "Laos": "Lao PDR",
    "Micronesia": "Micronesia, Fed. Sts.",
    "North Korea": "Korea, Dem. People's Rep.",
    "Russia": "Russian Federation",
    "Slovakia": "Slovak Republic",
    "South Korea": "Korea, Rep.",
    "Syria": "Syrian Arab Republic",
    "Turkey": "Turkiye",
    "United States": "United States",
    "Venezuela": "Venezuela, RB",
    "Vietnam": "Viet Nam",
    "Yemen": "Yemen, Rep.",
}


def get_text(url: str, cache_name: str, max_age_days: int = 30) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / cache_name
    if path.exists():
        age = time.time() - path.stat().st_mtime
        if age < max_age_days * 24 * 3600:
            return path.read_text()
    response = requests.get(url, headers=HEADERS, timeout=60)
    response.raise_for_status()
    text = response.content.decode("utf-8")
    path.write_text(text)
    return text


def normalize_name(name: str) -> str:
    name = NAME_OVERRIDES.get(name, name)
    name = re.sub(r"\s*\(details\)\s*", "", name)
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "".join(ch.lower() for ch in text if ch.isalnum())


def load_elo_teams() -> pd.DataFrame:
    text = get_text(TEAM_URL, "eloratings_teams.tsv")
    rows = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and not parts[0].endswith("_loc"):
            rows.append({"team_code": parts[0], "team": parts[1]})
    return pd.DataFrame(rows)


def load_world_bank_countries() -> pd.DataFrame:
    data = requests.get(WORLD_BANK_COUNTRIES_URL, headers=HEADERS, timeout=60).json()[1]
    rows = []
    for row in data:
        if row["region"]["value"] == "Aggregates":
            continue
        rows.append(
            {
                "iso2": row["iso2Code"],
                "iso3": row["id"],
                "country": row["name"],
                "wb_region": row["region"]["value"],
                "income_group": row["incomeLevel"]["value"],
            }
        )
    return pd.DataFrame(rows)


def latest_values(df: pd.DataFrame, value_name: str, as_of: int) -> pd.DataFrame:
    df = df[df["Year"].le(as_of) & df[value_name].notna()].copy()
    df = df.sort_values(["Code", "Year"]).groupby("Code", as_index=False).tail(1)
    return df[["Code", value_name]].rename(columns={"Code": "iso3"})


def load_owid_csv(url: str, cache_name: str) -> pd.DataFrame:
    text = get_text(url, cache_name)
    return pd.read_csv(io.StringIO(text))


def load_height(as_of_birth_year: int = 1996) -> pd.DataFrame:
    df = load_owid_csv(OWID_HEIGHT_URL, "owid_average_height_of_men.csv")
    value_col = [c for c in df.columns if c not in {"Entity", "Code", "Year"}][0]
    df = df[df["Year"].le(as_of_birth_year)]
    df = df.sort_values(["Code", "Year"]).groupby("Code", as_index=False).tail(1)
    return df[["Code", value_col]].rename(
        columns={"Code": "iso3", value_col: "male_height_cm"}
    )


def load_hours(as_of: int) -> pd.DataFrame:
    df = load_owid_csv(OWID_HOURS_URL, "owid_annual_working_hours_per_worker.csv")
    value_col = [c for c in df.columns if c not in {"Entity", "Code", "Year"}][0]
    return latest_values(
        df.rename(columns={value_col: "hours_worked_per_year"}),
        "hours_worked_per_year",
        as_of,
    )


def parse_percent(value) -> float | None:
    numbers = re.findall(r"(\d+(?:\.\d+)?)\s*%", str(value).replace(",", ""))
    if not numbers:
        return None
    return sum(float(n) for n in numbers) / len(numbers)


def load_catholic_share(wb_countries: pd.DataFrame) -> pd.DataFrame:
    html = get_text(WIKIPEDIA_CATHOLIC_URL, "wikipedia_catholic_church_by_country.html")
    tables = pd.read_html(io.StringIO(html))
    table = next(t for t in tables if "% Catholic" in t.columns and "Country" in t.columns)
    by_name = {
        normalize_name(row.country): row.iso3
        for row in wb_countries.itertuples()
    }
    rows = []
    for _, row in table.iterrows():
        country = row["Country"]
        iso3 = by_name.get(normalize_name(country))
        if not iso3:
            continue
        rows.append(
            {
                "iso3": iso3,
                "catholic_pct": parse_percent(row["% Catholic"]),
            }
        )
    return pd.DataFrame(rows).dropna(subset=["catholic_pct"])


def fetch_world_bank_indicator(indicator: str) -> list[dict]:
    url = f"https://api.worldbank.org/v2/country/all/indicator/{indicator}?format=json&per_page=20000"
    data = requests.get(url, headers=HEADERS, timeout=60).json()
    if not isinstance(data, list) or len(data) < 2:
        raise RuntimeError(f"Bad World Bank response for {indicator}")
    return data[1]


def load_world_bank_values(as_of: int) -> pd.DataFrame:
    out = None
    for name, indicator in WORLD_BANK_INDICATORS.items():
        rows = [
            {"iso3": r["countryiso3code"], "year": int(r["date"]), name: r["value"]}
            for r in fetch_world_bank_indicator(indicator)
            if r.get("countryiso3code") and r.get("value") is not None and int(r["date"]) <= as_of
        ]
        df = pd.DataFrame(rows).sort_values(["iso3", "year"])
        df = df.groupby("iso3", as_index=False).tail(1)[["iso3", name]]
        out = df if out is None else out.merge(df, on="iso3", how="outer")
    return out


def elo_rating_at(rows, team_code: str, day: date) -> int | None:
    latest = None
    for row in rows:
        if row.day > day:
            break
        if row.home == team_code:
            latest = row.home_elo
        elif row.away == team_code:
            latest = row.away_elo
    return latest


def monthly_dates(start: date, end: date) -> list[date]:
    out = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append(date(year, month, 1))
        month += 1
        if month == 13:
            year += 1
            month = 1
    return out


def load_team_elos(team_code: str, team: str) -> list:
    cache_name = "elo_" + quote(team.replace(" ", "_"), safe="") + ".tsv"
    text = get_text(f"https://www.eloratings.net/{team_url(team)}", cache_name)
    return parse_team_history(text)


def ten_year_elo(team_code: str, team: str, end: date) -> float | None:
    try:
        rows = load_team_elos(team_code, team)
    except HTTPError:
        return None
    samples = []
    for day in monthly_dates(date(end.year - 10, end.month, 1), end):
        rating = elo_rating_at(rows, team_code, day)
        if rating is not None:
            samples.append(rating)
    if len(samples) < 12:
        return None
    return round(float(np.mean(samples)), 1)


def world_cup_history() -> pd.DataFrame:
    appearances = Counter()
    titles = Counter()
    last_seen = {}
    for path in sorted((ROOT / "tournaments").glob("wc_*.yaml")):
        config = safe_load(path.read_text())
        year = config["year"]
        for team in config["teams"]:
            appearances[team] += 1
            last_seen[team] = max(last_seen.get(team, year), year)
        for stage in config["stages"]:
            if stage.get("id") == "final" and stage.get("actual", {}).get("winner"):
                titles[stage["actual"]["winner"]] += 1
    rows = []
    for team in appearances:
        rows.append(
            {
                "team_code": team,
                "world_cup_appearances": appearances[team],
                "world_cup_titles": titles[team],
                "last_world_cup": last_seen[team],
            }
        )
    return pd.DataFrame(rows)


def add_iso3(teams: pd.DataFrame, wb_countries: pd.DataFrame) -> pd.DataFrame:
    by_iso2 = wb_countries.set_index("iso2")["iso3"].to_dict()
    by_name = {
        normalize_name(row.country): row.iso3
        for row in wb_countries.itertuples()
    }

    def iso3(row):
        if row.team_code in ISO3_OVERRIDES:
            return ISO3_OVERRIDES[row.team_code]
        if row.team_code in by_iso2:
            return by_iso2[row.team_code]
        return by_name.get(normalize_name(row.team))

    teams = teams.copy()
    teams["iso3"] = teams.apply(iso3, axis=1)
    return teams


def build_dataset(as_of: int, elo_end: date) -> pd.DataFrame:
    teams = load_elo_teams()
    wb_countries = load_world_bank_countries()
    teams = add_iso3(teams, wb_countries)
    teams = teams[teams["iso3"].notna()].copy()

    teams["elo_10y_avg"] = [
        ten_year_elo(row.team_code, row.team, elo_end)
        for row in teams.itertuples()
    ]
    teams = teams[teams["elo_10y_avg"].notna()].copy()

    data = teams.merge(wb_countries[["iso3", "wb_region", "income_group"]], on="iso3", how="left")
    data = data.merge(load_world_bank_values(as_of), on="iso3", how="left")
    data = data.merge(load_height(), on="iso3", how="left")
    data = data.merge(load_hours(as_of), on="iso3", how="left")
    data = data.merge(load_catholic_share(wb_countries), on="iso3", how="left")
    data = data.merge(world_cup_history(), on="team_code", how="left")
    data["world_cup_appearances"] = data["world_cup_appearances"].fillna(0)
    data["world_cup_titles"] = data["world_cup_titles"].fillna(0)
    data["height_distance_from_181"] = (data["male_height_cm"] - 181).abs()
    data["log_population"] = np.log(data["population"])
    data["log_gdp_per_capita_usd"] = np.log(data["gdp_per_capita_usd"])
    data["log_gdp_per_capita_ppp"] = np.log(data["gdp_per_capita_ppp"])
    return data.sort_values("elo_10y_avg", ascending=False)


def model_score(df: pd.DataFrame, features: list[str]) -> tuple[float, float]:
    target = "elo_10y_avg"
    work = df[[target] + features].dropna(subset=[target])
    numeric = [
        f for f in features
        if pd.api.types.is_numeric_dtype(work[f])
    ]
    categorical = [f for f in features if f not in numeric]
    pre = ColumnTransformer(
        [
            ("num", Pipeline([("impute", SimpleImputer()), ("scale", StandardScaler())]), numeric),
            ("cat", Pipeline([
                ("impute", SimpleImputer(strategy="most_frequent")),
                ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
            ]), categorical),
        ]
    )
    model = Pipeline(
        [
            ("pre", pre),
            ("ridge", Ridge(alpha=25.0)),
        ]
    )
    x = work[features]
    y = work[target]
    model.fit(x, y)
    train_r2 = r2_score(y, model.predict(x))
    cv = KFold(n_splits=5, shuffle=True, random_state=1)
    cv_r2 = cross_val_score(model, x, y, cv=cv, scoring="r2").mean()
    return train_r2, cv_r2


def print_model_report(df: pd.DataFrame) -> None:
    models = {
        "GDP USD + pop + region + height": [
            "log_gdp_per_capita_usd",
            "log_population",
            "wb_region",
            "height_distance_from_181",
        ],
        "GDP PPP + pop + region + height": [
            "log_gdp_per_capita_ppp",
            "log_population",
            "wb_region",
            "height_distance_from_181",
        ],
        "+ migration": [
            "log_gdp_per_capita_ppp",
            "log_population",
            "wb_region",
            "height_distance_from_181",
            "foreign_born_pct",
        ],
        "+ hours worked": [
            "log_gdp_per_capita_ppp",
            "log_population",
            "wb_region",
            "height_distance_from_181",
            "foreign_born_pct",
            "hours_worked_per_year",
        ],
        "+ Catholic share": [
            "log_gdp_per_capita_ppp",
            "log_population",
            "wb_region",
            "height_distance_from_181",
            "foreign_born_pct",
            "hours_worked_per_year",
            "catholic_pct",
        ],
        "+ World Cup history": [
            "log_gdp_per_capita_ppp",
            "log_population",
            "wb_region",
            "height_distance_from_181",
            "foreign_born_pct",
            "hours_worked_per_year",
            "catholic_pct",
            "world_cup_appearances",
            "world_cup_titles",
        ],
    }
    print(f"Rows with Elo target: {len(df)}")
    print("Top 10 by ten-year Elo average:")
    print(df[["team_code", "team", "elo_10y_avg"]].head(10).to_string(index=False))
    print("\nModel comparison:")
    print("model                              train R2   5-fold CV R2")
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning, module="sklearn")
        for name, features in models.items():
            train_r2, cv_r2 = model_score(df, features)
            print(f"{name:<34} {train_r2:8.3f} {cv_r2:12.3f}")
    missing = df[
        df[["gdp_per_capita_ppp", "population", "male_height_cm", "hours_worked_per_year"]]
        .isna()
        .any(axis=1)
    ]
    if not missing.empty:
        print("\nRows with missing core covariates:")
        print(missing[["team_code", "team", "iso3"]].head(30).to_string(index=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", type=int, default=2024)
    parser.add_argument("--elo-end", default="2026-06-01")
    parser.add_argument("--write", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    elo_end = date.fromisoformat(args.elo_end)
    df = build_dataset(args.as_of, elo_end)
    print_model_report(df)
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.write, index=False)
        print(f"\nWrote {args.write}")


if __name__ == "__main__":
    main()
