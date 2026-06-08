"""Fetch ELO ratings and match schedule from eloratings.net.

Usage:
    python fetch_elo.py <tournament.yaml>

Updates the YAML in place with:
- `teams: <code>: elo:` — rating each team carried into its first match on/after
  the tournament's `cutoff` date.
- `stages: groups: schedule:` — ordered (date, group, home, away) list for
  each group stage.
- `stages: bracket: matches: <id>: date:` — date per knockout match.

The schedule fetch needs `match_code:` at the YAML top level (e.g. WC, EU)
matching the tournament code in `<year>_results.tsv`. For tournaments without
played matches, the schedule fetch silently no-ops.
"""
from __future__ import annotations

import argparse
import calendar
import sys
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml

# Reuse the simulator's ranking & slot-resolution logic so we agree on
# tiebreakers and bracket plumbing.
sys.path.insert(0, str(Path(__file__).parent))
from simulate import Match as SimMatch, rank_group, resolve, team_record, thirds_assign  # noqa: E402
from yaml_compat import safe_load as _yaml_load, safe_dump as _yaml_dump  # noqa: E402

BASE = "https://www.eloratings.net"


def http_get(path: str) -> str:
    req = Request(f"{BASE}/{path}", headers={"User-Agent": "football-predictions/0.1"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def load_team_names() -> dict[str, str]:
    out = {}
    for line in http_get("en.teams.tsv").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


# eloratings consolidates historical teams under modern names (WG→DE→"Germany",
# SU→RU→"Russia"). Override the display name for historical eras where the
# football team used a different identity.
# Each entry is (code, from_year_inclusive, before_year, name).
HISTORICAL_NAMES = [
    ("DE", 1949, 1991, "West Germany"),
    ("RU", None, 1992, "Soviet Union"),
    ("CZ", None, 1993, "Czechoslovakia"),
    ("RS", None, 2003, "Yugoslavia"),             # pre-2003
    ("RS", 2003, 2007, "Serbia and Montenegro"),  # 2003-2006
    ("CD", None, 1997, "Zaire"),
]


def historical_name(code: str, year: int | None) -> str | None:
    if year is None:
        return None
    for c, start, before, name in HISTORICAL_NAMES:
        if code == c and (start is None or year >= start) and year < before:
            return name
    return None


def load_team_aliases() -> dict[str, str]:
    """Map historical codes (WG, SU, YU, …) to their modern equivalents."""
    out = {}
    for line in http_get("teams.tsv").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


def team_url(name: str) -> str:
    # eloratings' per-team URLs are ASCII (Curaçao → Curacao.tsv, etc.).
    ascii_name = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    )
    return quote(ascii_name.replace(" ", "_")) + ".tsv"


@dataclass
class HistoryRow:
    day: date
    home: str
    away: str
    home_elo: int
    away_elo: int


def parse_team_history(tsv: str) -> list[HistoryRow]:
    out = []
    for line in tsv.splitlines():
        cols = line.split("\t")
        if len(cols) < 12:
            continue
        try:
            d = date(int(cols[0]), int(cols[1]), int(cols[2]))
            out.append(HistoryRow(d, cols[3], cols[4], int(cols[10]), int(cols[11])))
        except ValueError:
            continue
    return out


def elo_at(rows: list[HistoryRow], code: str, cutoff: date) -> int | None:
    """ELO of `code` on `cutoff` — the rating going into their first match
    on or after cutoff. If no such match exists (tournament hasn't started
    and no friendlies scheduled past cutoff), fall back to the pre-match
    rating of their most recent game — off by one match's worth of change."""
    for r in rows:
        if r.day < cutoff:
            continue
        if r.home == code:
            return r.home_elo
        if r.away == code:
            return r.away_elo
    for r in reversed(rows):
        if r.home == code:
            return r.home_elo
        if r.away == code:
            return r.away_elo
    return None


def months_before(day: date, months: int) -> date:
    """Return the same day approximately `months` earlier, clamped to the
    target month's final valid day."""
    month_index = day.year * 12 + day.month - 1 - months
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def fetch_team_elos(code: str, name: str, cutoff: date) -> tuple[int | None, int | None]:
    tsv = http_get(team_url(name))
    rows = parse_team_history(tsv)
    return elo_at(rows, code, cutoff), elo_at(rows, code, months_before(cutoff, 9))


def update_elos(config: dict, cutoff: date, year: int | None = None) -> list[tuple[str, str]]:
    names = load_team_names()
    aliases = load_team_aliases()
    failed = []
    for code, info in config["teams"].items():
        # Apply alias (e.g. MK → NM for North Macedonia, WG → DE) before
        # name lookup, so YAML can use either the old or the modern code.
        canonical_code = aliases.get(code, code)
        canonical = names.get(canonical_code) or info.get("name")
        if not canonical:
            failed.append((code, "no name in YAML or en.teams.tsv"))
            continue
        try:
            elo, elo_9m = fetch_team_elos(code, canonical, cutoff)
        except Exception as e:
            failed.append((code, str(e)))
            continue
        if elo is None:
            failed.append((code, f"no match on/after {cutoff}"))
            continue
        info["elo"] = elo
        if elo_9m is not None:
            info["elo_9m"] = elo_9m
        info["name"] = historical_name(canonical_code, year) or canonical
        trend = f" ({elo - elo_9m:+d} over 9m)" if elo_9m is not None else ""
        print(f"  {code} {info['name']:<30} {elo}{trend}")
        time.sleep(0.1)
    return failed


def update_recency_elos(config: dict, cutoff: date) -> list[tuple[str, str]]:
    """Add only the nine-month ELO snapshot, preserving current ratings and
    all tournament structure/results."""
    names = load_team_names()
    aliases = load_team_aliases()
    failed = []
    snapshot_day = months_before(cutoff, 9)
    for code, info in config["teams"].items():
        canonical_code = aliases.get(code, code)
        canonical = names.get(canonical_code) or info.get("name")
        if not canonical:
            failed.append((code, "no name in YAML or en.teams.tsv"))
            continue
        try:
            tsv = http_get(team_url(canonical))
            elo_9m = elo_at(parse_team_history(tsv), code, snapshot_day)
        except Exception as e:
            failed.append((code, str(e)))
            continue
        if elo_9m is None:
            failed.append((code, f"no ELO around {snapshot_day}"))
            continue
        info["elo_9m"] = elo_9m
        trend = info["elo"] - elo_9m
        print(f"  {code} {info['name']:<30} {elo_9m} → {info['elo']} ({trend:+d})")
        time.sleep(0.1)
    return failed


@dataclass
class ResultRow:
    day: date
    home: str
    away: str
    home_goals: int
    away_goals: int


def fetch_results(year: int, match_code: str, aliases: dict[str, str] | None = None) -> list[ResultRow]:
    tsv = http_get(f"{year}_results.tsv")
    aliases = aliases or {}
    out = []
    for line in tsv.splitlines():
        cols = line.split("\t")
        if len(cols) < 8 or cols[7] != match_code:
            continue
        try:
            d = date(int(cols[0]), int(cols[1]), int(cols[2]))
            home = aliases.get(cols[3], cols[3])
            away = aliases.get(cols[4], cols[4])
            out.append(ResultRow(d, home, away, int(cols[5]), int(cols[6])))
        except ValueError:
            continue
    out.sort(key=lambda r: r.day)
    return out


def update_schedule(config: dict, results: list[ResultRow]) -> list[str]:
    """Walk results chronologically; assign each to a group stage match or a
    bracket slot. Returns list of warnings."""
    teams = config["teams"]
    criteria = config.get(
        "tiebreakers",
        ["points", "gd", "gf", "h2h_points", "h2h_gd", "random"],
    )

    slots: dict[str, str] = {}
    group_matches: dict[tuple[str, str], list[SimMatch]] = defaultdict(list)
    team_stats: dict[str, dict] = {}
    processed_thirds_pools: set[str] = set()
    schedules: dict[str, list[list]] = {
        s["id"]: [] for s in config["stages"] if s["type"] == "groups"
    }
    bracket_dates: dict[tuple[str, str], date] = {}
    bracket_actuals: dict[tuple[str, str], dict] = {}
    single_match_dates: dict[str, date] = {}
    single_match_actuals: dict[str, dict] = {}
    warnings: list[str] = []

    def maybe_run_thirds_pools():
        for stage in config["stages"]:
            if stage["type"] != "thirds_pool" or stage["id"] in processed_thirds_pools:
                continue
            source = next(
                (s for s in config["stages"] if s["id"] == stage["source"]), None
            )
            if source is None:
                continue
            if not all(
                f"{stage['source']}.{g}.{stage['rank']}" in slots
                for g in source["groups"]
            ):
                continue
            thirds_assign(stage, slots, team_stats, source)
            processed_thirds_pools.add(stage["id"])

    def resolved_group_codes(stage):
        return {
            grp_name: [resolve(c, slots, teams) for c in codes]
            for grp_name, codes in stage["groups"].items()
        }

    def try_assign_group(r: ResultRow) -> bool:
        for stage in config["stages"]:
            if stage["type"] != "groups":
                continue
            try:
                gcodes = resolved_group_codes(stage)
            except KeyError:
                continue  # this stage's slots not all resolved yet
            for grp_name, codes in gcodes.items():
                if r.home in codes and r.away in codes:
                    sid = stage["id"]
                    n = len(codes)
                    # Expected match count: explicit pairings if declared
                    # (seeded-group formats like WC 1954), else full round-robin.
                    pairings = (stage.get("pairings") or {}).get(grp_name)
                    n_expected = len(pairings) if pairings else n * (n - 1) // 2
                    # Group's expected matches already played: don't claim
                    # (probably a playoff or a later same-pairing third-place
                    # / final).
                    if len(group_matches[(sid, grp_name)]) >= n_expected:
                        return False
                    # Reorder to match YAML group position, so override keys
                    # generated by the simulator's i<j loop will line up.
                    if codes.index(r.home) < codes.index(r.away):
                        h, a, hg, ag = r.home, r.away, r.home_goals, r.away_goals
                    else:
                        h, a, hg, ag = r.away, r.home, r.away_goals, r.home_goals
                    schedules[sid].append([r.day, grp_name, h, a, hg, ag])
                    group_matches[(sid, grp_name)].append(SimMatch(h, a, hg, ag))
                    if len(group_matches[(sid, grp_name)]) == n_expected:
                        # Exclude `random` so ordering is reproducible; if
                        # teams remain tied after deterministic criteria, the
                        # bracket fixup pass will swap positions based on
                        # who actually appeared in subsequent matches.
                        det = [c for c in criteria if c != "random"]
                        matches = group_matches[(sid, grp_name)]
                        for code in codes:
                            team_stats[code] = {
                                **team_record(code, matches),
                                "group": grp_name,
                            }
                        ranked = rank_group(codes, matches, det)
                        for pos, code in enumerate(ranked, start=1):
                            slots[f"{sid}.{grp_name}.{pos}"] = code
                        maybe_run_thirds_pools()
                    return True
        return False

    def swap_slot(slot_ref: str, expected: str) -> bool:
        """If a knockout result names a team we didn't put in this slot, swap
        positions in the source group so the actual team lands here. Used to
        repair real-world fair-play / drawing-of-lots tiebreakers we don't
        model."""
        if slot_ref not in slots:
            return False
        current = slots[slot_ref]
        # Find expected's current slot — must be in the same group stage.
        prefix = slot_ref.rsplit(".", 1)[0]  # e.g. "groups.H"
        other = next((k for k, v in slots.items()
                      if v == expected and k.startswith(prefix + ".")), None)
        if other is None:
            return False
        slots[slot_ref] = expected
        slots[other] = current
        return True

    def try_assign_bracket(r: ResultRow, winner: str) -> bool:
        # First pass: strict match.
        for stage in config["stages"]:
            sid = stage["id"]
            if stage["type"] == "bracket":
                for mid, mdef in stage["matches"].items():
                    if f"{sid}.{mid}.W" in slots:
                        continue
                    try:
                        rh = resolve(mdef["home"], slots, teams)
                        ra = resolve(mdef["away"], slots, teams)
                    except KeyError:
                        continue
                    matched = {rh, ra} == {r.home, r.away}
                    # Partial-match repair: one slot right, one wrong → likely
                    # a missed real-world tiebreaker. Swap and reassign.
                    if not matched and rh in (r.home, r.away):
                        expected = r.away if rh == r.home else r.home
                        if swap_slot(mdef["away"], expected):
                            ra = expected
                            matched = True
                    elif not matched and ra in (r.home, r.away):
                        expected = r.away if ra == r.home else r.home
                        if swap_slot(mdef["home"], expected):
                            rh = expected
                            matched = True
                    if matched:
                        bracket_dates[(sid, mid)] = r.day
                        score = ([r.home_goals, r.away_goals] if r.home == rh
                                 else [r.away_goals, r.home_goals])
                        bracket_actuals[(sid, mid)] = {"winner": winner, "score": score}
                        loser = r.away if winner == r.home else r.home
                        slots[f"{sid}.{mid}.W"] = winner
                        slots[f"{sid}.{mid}.L"] = loser
                        return True
            elif stage["type"] == "match":
                if f"{sid}.W" in slots:
                    continue
                try:
                    rh = resolve(stage["home"], slots, teams)
                    ra = resolve(stage["away"], slots, teams)
                except KeyError:
                    continue
                if {rh, ra} == {r.home, r.away}:
                    single_match_dates[sid] = r.day
                    score = ([r.home_goals, r.away_goals] if r.home == rh
                             else [r.away_goals, r.home_goals])
                    single_match_actuals[sid] = {"winner": winner, "score": score}
                    loser = r.away if winner == r.home else r.home
                    slots[f"{sid}.W"] = winner
                    slots[f"{sid}.L"] = loser
                    return True
        return False

    def infer_winner(r: ResultRow, idx: int) -> str:
        """For drawn knockouts, the winner is the team that plays in the
        latest subsequent match: finals are scheduled after 3rd-place
        playoffs, so the SF winner's last match comes after the loser's."""
        if r.home_goals > r.away_goals:
            return r.home
        if r.away_goals > r.home_goals:
            return r.away
        home_latest = away_latest = None
        for later in results[idx + 1:]:
            if r.home in (later.home, later.away):
                home_latest = later.day
            if r.away in (later.home, later.away):
                away_latest = later.day
        if home_latest is None and away_latest is None:
            # Last match of the tournament went to penalties; we have no
            # signal to pick a winner. Default to home and warn so the
            # caller can correct the YAML manually.
            warnings.append(
                f"drawn last match {r.day} {r.home}-{r.away} "
                f"{r.home_goals}-{r.away_goals} — defaulted winner to {r.home}; "
                f"verify and fix `actual.winner` in the YAML if wrong"
            )
            return r.home
        if away_latest is None:
            return r.home
        if home_latest is None:
            return r.away
        return r.home if home_latest >= away_latest else r.away

    for i, r in enumerate(results):
        if try_assign_group(r):
            continue
        if try_assign_bracket(r, infer_winner(r, i)):
            continue
        warnings.append(f"unassigned: {r.day} {r.home}-{r.away} {r.home_goals}-{r.away_goals}")

    # Reorder each group's codes to match the resolved standings so the
    # simulator's deterministic tiebreaker (which falls through to YAML order)
    # agrees with reality. This matters when real tournaments resolve true
    # ties via fair-play / drawing-of-lots that we don't model.
    reordered: set[tuple[str, str]] = set()
    for stage in config["stages"]:
        if stage["type"] != "groups":
            continue
        for grp_name, codes in list(stage["groups"].items()):
            # Only reorder stages whose codes are bare team codes; later
            # group stages reference slots from prior stages.
            if any("." in c for c in codes):
                continue
            ordered = []
            for pos in range(1, len(codes) + 1):
                c = slots.get(f"{stage['id']}.{grp_name}.{pos}")
                if c:
                    ordered.append(c)
            for c in codes:
                if c not in ordered:
                    ordered.append(c)
            if ordered != codes:
                stage["groups"][grp_name] = ordered
                reordered.add((stage["id"], grp_name))
    # Re-normalize schedule entries' home/away to the new code order, only
    # for groups whose codes actually changed.
    for stage in config["stages"]:
        if stage["type"] != "groups":
            continue
        sid = stage["id"]
        new_sched = []
        for entry in schedules[sid]:
            day, grp, h, a, hg, ag = entry
            if (sid, grp) not in reordered:
                new_sched.append(entry)
                continue
            codes = stage["groups"][grp]
            if codes.index(h) < codes.index(a):
                new_sched.append([day, grp, h, a, hg, ag])
            else:
                new_sched.append([day, grp, a, h, ag, hg])
        schedules[sid] = new_sched

    # Write back
    for stage in config["stages"]:
        if stage["type"] == "groups":
            sched = schedules[stage["id"]]
            if sched:
                stage["schedule"] = sched
        elif stage["type"] == "bracket":
            for mid, mdef in stage["matches"].items():
                d = bracket_dates.get((stage["id"], mid))
                if d is not None:
                    mdef["date"] = d
                a = bracket_actuals.get((stage["id"], mid))
                if a is not None:
                    mdef["actual"] = a
        elif stage["type"] == "match":
            d = single_match_dates.get(stage["id"])
            if d is not None:
                stage["date"] = d
            a = single_match_actuals.get(stage["id"])
            if a is not None:
                stage["actual"] = a

    return warnings


def update_tournament(yaml_path: Path, recency_only: bool = False) -> None:
    data = _yaml_load(yaml_path.read_text())

    cutoff = data.get("cutoff")
    if cutoff is None:
        raise SystemExit(f"{yaml_path}: needs a top-level `cutoff: YYYY-MM-DD`")
    if isinstance(cutoff, str):
        cutoff = date.fromisoformat(cutoff)

    if recency_only:
        print("Fetching nine-month ELO snapshots…")
        failed = update_recency_elos(data, cutoff)
        yaml_path.write_text(_yaml_dump(data, sort_keys=False, allow_unicode=True))
        if failed:
            print(f"\n*** {len(failed)} snapshot fetch failure(s) ***", file=sys.stderr)
            for code, reason in failed:
                print(f"  {code}: {reason}", file=sys.stderr)
        return

    print("Fetching ELO ratings…")
    failed = update_elos(data, cutoff, data.get("year"))

    match_code = data.get("match_code")
    year = data.get("year")
    sched_warnings = []
    if match_code and year:
        print(f"\nFetching schedule for {year} {match_code}…")
        aliases = load_team_aliases()
        results = fetch_results(int(year), match_code, aliases)
        if results:
            sched_warnings = update_schedule(data, results)
            print(f"  schedule: {sum(1 for r in results)} matches assigned")
        else:
            print("  no results found yet")

    yaml_path.write_text(_yaml_dump(data, sort_keys=False, allow_unicode=True))

    if failed:
        print(f"\n*** {len(failed)} ELO fetch failure(s) ***", file=sys.stderr)
        for code, reason in failed:
            print(f"  {code}: {reason}", file=sys.stderr)
        print("These teams have no ELO; the simulator will hang.", file=sys.stderr)
    if sched_warnings:
        print("\nSchedule warnings:", file=sys.stderr)
        for w in sched_warnings:
            print(f"  {w}", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("yaml_file", type=Path)
    p.add_argument(
        "--recency-only",
        action="store_true",
        help="only add nine-month ELO snapshots; preserve ratings and tournament data",
    )
    args = p.parse_args()
    if args.yaml_file.is_dir():
        if not args.recency_only:
            raise SystemExit("directory input is only supported with --recency-only")
        paths = sorted(args.yaml_file.glob("*.yaml"))
        for path in paths:
            data = _yaml_load(path.read_text())
            if all("elo_9m" in info for info in data.get("teams", {}).values()):
                continue
            print(f"\n{path.name}")
            update_tournament(path, recency_only=True)
        return
    update_tournament(args.yaml_file, recency_only=args.recency_only)


if __name__ == "__main__":
    main()
