"""Predict the most-likely scoreline of each match from the ELO model.

`simulate.py` samples thousands of tournaments to estimate how often each team
reaches each round. This tool answers a narrower, concrete question: given the
model, what is the single most-likely *score* of one match? That is what you
need to fill in a score-prediction pool — one exact scoreline per fixture.

Three layers, applied in order:

  1. Model modal score. The goal model (shared with `simulate.py`) turns the ELO
     gap into an expected-goals (xG) value per team. The most-likely scoreline
     is the mode of each team's Poisson distribution, i.e. ``floor(xG)``.

  2. Market blend (optional). If a fixture carries betting lines in the YAML's
     ``odds`` block, the Asian-handicap and over/under lines are converted to a
     "market xG" and blended 80/20 with the model xG before taking the mode. The
     market prices in injuries and form the pre-tournament ELO snapshot cannot
     see, and it corrects the flat host bonus for weak hosts.

  3. Blowout tier. When the *raw* ELO gap (no host bonus) is >= 440 the match is
     so lopsided that ``floor(xG)`` understates it; we call an outright 4-0.

The market blend and blowout tier are what turn the raw modal score into the
concrete scorelines you would actually enter in a pool; ``--backtest`` scores
the whole approach against historical results, and ``--outcome-aware`` trades
the plain modal score for the likeliest result (fewer over-predicted draws).

Usage:
    python3 predict.py tournaments/wc_2026.yaml
    python3 predict.py tournaments/wc_2026.yaml --json
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

from simulate import (
    BASE_XG,
    ELO_TO_XG,
    MIN_XG,
    Match,
    effective_elos,
    rank_group,
    resolve,
    team_record,
    thirds_assign,
)
from yaml_compat import safe_load

BLOWOUT_GAP_DEFAULT = 440    # raw ELO gap that calls an outright 4-0
MARKET_WEIGHT_DEFAULT = 0.8  # weight on market xG when blending with model xG


def market_xg(ah: float, ou: float) -> tuple[float, float]:
    """Convert bookmaker lines to expected goals for (home, away).

    The Asian-handicap line ``ah`` is the negative of the home team's
    supremacy; the over/under line ``ou`` is the expected total. Solving
    ``xg_home - xg_away = -ah`` and ``xg_home + xg_away = ou`` gives the split.
    """
    supremacy = -ah
    return (
        max(MIN_XG, (ou + supremacy) / 2),
        max(MIN_XG, (ou - supremacy) / 2),
    )


def poisson_mode(mean: float) -> int:
    """Mode of a Poisson(mean): floor(mean), floored at our minimum xG."""
    return math.floor(max(MIN_XG, mean))


def outcome_probs(xg_a: float, xg_b: float) -> tuple[float, float, float]:
    """Analytic (win, draw, loss) for team A under independent Poissons."""
    max_goals = 20
    dists = []
    for mean in (xg_a, xg_b):
        col = [math.exp(-mean)]
        for k in range(1, max_goals + 1):
            col.append(col[k - 1] * mean / k)
        dists.append(col)
    win = draw = loss = 0.0
    for a in range(max_goals + 1):
        for b in range(max_goals + 1):
            p = dists[0][a] * dists[1][b]
            if a > b:
                win += p
            elif a == b:
                draw += p
            else:
                loss += p
    total = win + draw + loss
    return win / total, draw / total, loss / total


def scoreline_probabilities(
    xg_a: float, xg_b: float, top: int | None = None, max_goals: int = 10
) -> list[tuple[tuple[int, int], float]]:
    """Scorelines ranked by probability (independent Poissons). With ``top``
    set, return only that many of the most likely ones."""
    dists = []
    for mean in (xg_a, xg_b):
        col = [math.exp(-mean)]
        for k in range(1, max_goals + 1):
            col.append(col[k - 1] * mean / k)
        dists.append(col)
    grid = [
        ((a, b), dists[0][a] * dists[1][b])
        for a in range(max_goals + 1)
        for b in range(max_goals + 1)
    ]
    grid.sort(key=lambda kv: -kv[1])
    return grid[:top] if top else grid


# Pool scoring: an exact scoreline is worth 3 points, a correct result 1.
POOL_EXACT = 3
POOL_TOTO = 1


def _sign(score) -> int:
    return (score[0] > score[1]) - (score[0] < score[1])


def pool_points(pred, actual) -> tuple[int, str]:
    """Points and category for a predicted vs actual scoreline."""
    pred, actual = tuple(pred), tuple(actual)
    if pred == actual:
        return POOL_EXACT, "exact"
    if _sign(pred) == _sign(actual):
        return POOL_TOTO, "toto"
    return 0, "miss"


def outcome_aware_score(xg_a: float, xg_b: float) -> tuple[int, int]:
    """The most likely scoreline *consistent with the most likely result*.

    The plain modal scoreline (floor(xG), floor(xG)) over-predicts draws: for
    evenly matched teams 1-1 is the single likeliest score even though a draw is
    not the likeliest *result*. Backtesting on historical pools shows this costs
    points; conditioning the score on the favoured win/draw/loss outcome first
    recovers them.
    """
    win, draw, loss = outcome_probs(xg_a, xg_b)
    best = max(("w", win), ("d", draw), ("l", loss), key=lambda t: t[1])[0]

    def consistent(s) -> bool:
        if best == "w":
            return s[0] > s[1]
        if best == "d":
            return s[0] == s[1]
        return s[0] < s[1]

    for s, _ in scoreline_probabilities(xg_a, xg_b):
        if consistent(s):
            return s
    return (1, 1)


def predict_score(
    elo_a: int,
    elo_b: int,
    *,
    raw_diff: int | None = None,
    market: dict | None = None,
    market_weight: float = MARKET_WEIGHT_DEFAULT,
    blowout: int | None = BLOWOUT_GAP_DEFAULT,
    outcome_aware: bool = False,
    top: int | None = None,
) -> dict:
    """Most-likely scoreline for A vs B.

    ``elo_a``/``elo_b`` are effective ELOs (host bonus included) and drive the
    xG model. ``raw_diff`` is the no-host ELO gap that gates the blowout tier;
    it defaults to ``elo_a - elo_b``. ``market`` is an optional ``{ah, ou}``.
    With ``outcome_aware`` the score is conditioned on the likeliest result
    instead of the plain modal score. With ``top`` set, the result also carries
    the N most likely scorelines.
    """
    if raw_diff is None:
        raw_diff = elo_a - elo_b
    xg_a = max(MIN_XG, BASE_XG + (elo_a - elo_b) * ELO_TO_XG)
    xg_b = max(MIN_XG, BASE_XG - (elo_a - elo_b) * ELO_TO_XG)
    blended = False
    if market:
        m_a, m_b = market_xg(market["ah"], market["ou"])
        xg_a = market_weight * m_a + (1 - market_weight) * xg_a
        xg_b = market_weight * m_b + (1 - market_weight) * xg_b
        blended = True
    a, b = (outcome_aware_score(xg_a, xg_b) if outcome_aware
            else (poisson_mode(xg_a), poisson_mode(xg_b)))
    is_blowout = False
    if blowout is not None:
        if raw_diff >= blowout:
            a, b, is_blowout = 4, 0, True
        elif raw_diff <= -blowout:
            a, b, is_blowout = 0, 4, True
    result = {
        "score": (a, b),
        "xg": (round(xg_a, 2), round(xg_b, 2)),
        "probs": outcome_probs(xg_a, xg_b),
        "market": blended,
        "blowout": is_blowout,
    }
    if top:
        result["top"] = [
            ([s[0], s[1]], round(p, 4))
            for s, p in scoreline_probabilities(xg_a, xg_b, top=top)
        ]
    return result


def market_for(odds: dict, home: str, away: str) -> dict | None:
    """Look up directional lines for a fixture, swapping if listed reversed."""
    if not odds:
        return None
    if (key := f"{home}-{away}") in odds:
        return odds[key]
    if (key := f"{away}-{home}") in odds:
        flipped = odds[key]
        return {"ah": -flipped["ah"], "ou": flipped["ou"]}
    return None


def _group_fixtures(stage: dict, slots: dict, teams: dict):
    """Yield (group, home, away, actual) for a group stage. Uses the real
    schedule (with results) when present, else a round-robin (no results).
    ``actual`` is the (home, away) goals when known, else None."""
    schedule = stage.get("schedule")
    if schedule:
        for _, group, home, away, hg, ag in schedule:
            actual = (hg, ag) if hg is not None and ag is not None else None
            yield group, home, away, actual
        return
    pairings = stage.get("pairings") or {}
    for group_name, codes in stage["groups"].items():
        members = [resolve(c, slots, teams) for c in codes]
        if group_name in pairings:
            pairs = [
                (resolve(a, slots, teams), resolve(b, slots, teams))
                for a, b in pairings[group_name]
            ]
        else:
            pairs = [
                (members[i], members[j])
                for i in range(len(members))
                for j in range(i + 1, len(members))
            ]
        for home, away in pairs:
            yield group_name, home, away, None


def build_actual_slots(config: dict) -> tuple[dict, dict]:
    """Resolve slots from *actual* results only (deterministic).

    Lets us predict knockout ties whose participants are already decided. Group
    positions are filled only once a group has played every fixture; brackets
    use recorded winners. Unresolved slots are simply left absent.
    """
    teams = config["teams"]
    criteria = config.get(
        "tiebreakers", ["points", "gd", "gf", "h2h_points", "h2h_gd", "random"]
    )
    slots: dict[str, str] = {}
    team_stats: dict[str, dict] = {}

    for stage in config["stages"]:
        sid = stage["id"]
        if stage["type"] == "groups":
            played: dict[str, list[Match]] = {g: [] for g in stage["groups"]}
            for entry in stage.get("schedule") or []:
                _, group, home, away, hg, ag = entry
                if hg is not None and ag is not None:
                    played[group].append(Match(home, away, hg, ag))
            for group_name, codes in stage["groups"].items():
                members = [resolve(c, slots, teams) for c in codes]
                matches = played[group_name]
                for code in members:
                    rec = team_record(code, matches)
                    team_stats[code] = {**rec, "group": group_name}
                expected = len(members) * (len(members) - 1) // 2
                if len(matches) >= expected and expected:
                    for pos, code in enumerate(
                        rank_group(members, matches, criteria), start=1
                    ):
                        slots[f"{sid}.{group_name}.{pos}"] = code
        elif stage["type"] == "thirds_pool":
            source = next(
                (s for s in config["stages"] if s["id"] == stage["source"]), None
            )
            if source is not None:
                thirds_assign(stage, slots, team_stats, source)
        elif stage["type"] == "bracket":
            for m_id, m_def in stage["matches"].items():
                actual = m_def.get("actual")
                if not actual or not actual.get("winner"):
                    continue
                try:
                    a = resolve(m_def["home"], slots, teams)
                    b = resolve(m_def["away"], slots, teams)
                except KeyError:
                    continue
                w = actual["winner"]
                slots[f"{sid}.{m_id}.W"] = w
                slots[f"{sid}.{m_id}.L"] = b if w == a else a
        elif stage["type"] == "match":
            actual = stage.get("actual")
            if actual and actual.get("winner"):
                try:
                    a = resolve(stage["home"], slots, teams)
                    b = resolve(stage["away"], slots, teams)
                except KeyError:
                    continue
                w = actual["winner"]
                slots[f"{sid}.W"] = w
                slots[f"{sid}.L"] = b if w == a else a
    return slots, team_stats


STAGE_NAMES = {
    "r16": "Round of 16",
    "qf": "Quarter-finals",
    "sf": "Semi-finals",
    "final": "Final",
    "third_place": "Third place",
}


def iter_fixtures(config: dict, slots: dict):
    """Yield a fixture dict {stage, label, home, away, actual} for every match
    with both teams concrete. ``actual`` is the (home, away) result when known,
    else None. Group fixtures are always concrete; knockout ties only once their
    feeder results are in."""
    teams = config["teams"]
    for stage in config["stages"]:
        sid = stage["id"]
        label = STAGE_NAMES.get(sid, sid.replace("_", " ").title())
        if stage["type"] == "groups":
            for group, home, away, actual in _group_fixtures(stage, slots, teams):
                yield {"stage": sid, "label": f"Group {group}",
                       "home": home, "away": away, "actual": actual}
        elif stage["type"] == "bracket":
            for m_id, m_def in stage["matches"].items():
                try:
                    home = resolve(m_def["home"], slots, teams)
                    away = resolve(m_def["away"], slots, teams)
                except KeyError:
                    continue
                score = (m_def.get("actual") or {}).get("score")
                yield {"stage": sid, "label": label, "home": home, "away": away,
                       "actual": tuple(score) if score else None}
        elif stage["type"] == "match":
            try:
                home = resolve(stage["home"], slots, teams)
                away = resolve(stage["away"], slots, teams)
            except KeyError:
                continue
            score = (stage.get("actual") or {}).get("score")
            yield {"stage": sid, "label": label, "home": home, "away": away,
                   "actual": tuple(score) if score else None}


def predict_tournament(
    config: dict,
    *,
    blowout: int | None = BLOWOUT_GAP_DEFAULT,
    market_weight: float = MARKET_WEIGHT_DEFAULT,
    use_market: bool = True,
    outcome_aware: bool = False,
    top: int | None = None,
) -> list[dict]:
    teams = config["teams"]
    eff = effective_elos(config)
    odds = config.get("odds") if use_market else None
    slots, _ = build_actual_slots(config)

    out = []
    for fx in iter_fixtures(config, slots):
        home, away = fx["home"], fx["away"]
        market = market_for(odds, home, away) if odds else None
        pred = predict_score(
            eff[home],
            eff[away],
            raw_diff=teams[home]["elo"] - teams[away]["elo"],
            market=market,
            market_weight=market_weight,
            blowout=blowout,
            outcome_aware=outcome_aware,
            top=top,
        )
        rec = {**fx, **pred}
        if fx["actual"] is not None:
            rec["points"], rec["category"] = pool_points(pred["score"], fx["actual"])
        out.append(rec)
    return out


BASELINES = {
    "always 1-1": lambda h, a, teams: (1, 1),
    "always 1-0 home": lambda h, a, teams: (1, 0),
    "favourite 1-0": lambda h, a, teams:
        (1, 0) if teams[h]["elo"] >= teams[a]["elo"] else (0, 1),
}


def backtest(configs: list[tuple[str, dict]], **opts) -> dict:
    """Score the model (modal and outcome-aware) and naive baselines on every
    played group match across the given tournaments (pool scoring), and count
    the actual result distribution. Returns {systems, distribution}."""
    opts.pop("top", None)
    opts.pop("outcome_aware", None)
    systems = ["model (modal)", "model (outcome-aware)", *BASELINES]
    agg = {s: {"n": 0, "points": 0, "exact": 0, "toto": 0} for s in systems}
    dist = {"home": 0, "draw": 0, "away": 0}
    for _, config in configs:
        teams = config["teams"]
        modal = predict_tournament(config, **opts)
        aware = predict_tournament(config, outcome_aware=True, **opts)
        for m_rec, a_rec in zip(modal, aware):
            if m_rec["actual"] is None or not m_rec["label"].startswith("Group "):
                continue
            actual = m_rec["actual"]
            dist["home" if actual[0] > actual[1]
                 else "draw" if actual[0] == actual[1] else "away"] += 1
            preds = {
                "model (modal)": tuple(m_rec["score"]),
                "model (outcome-aware)": tuple(a_rec["score"]),
            }
            for name, fn in BASELINES.items():
                preds[name] = fn(m_rec["home"], m_rec["away"], teams)
            for system, ps in preds.items():
                pts, cat = pool_points(ps, actual)
                agg[system]["n"] += 1
                agg[system]["points"] += pts
                if cat in ("exact", "toto"):
                    agg[system][cat] += 1
    return {"systems": agg, "distribution": dist}


def _load_configs(path: Path, completed_only: bool) -> list[tuple[str, dict]]:
    files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    out = []
    for f in files:
        config = safe_load(f.read_text())
        if not config:
            continue
        if completed_only and config.get("year", 0) >= 2026:
            continue
        out.append((f.name, config))
    return out


def run_backtest(path: Path, as_json: bool, opts: dict) -> None:
    configs = _load_configs(path, completed_only=True)
    result = backtest(configs, **opts)
    if as_json:
        print(json.dumps(result, default=str))
        return
    agg, dist = result["systems"], result["distribution"]
    n = agg["model (modal)"]["n"]
    total = sum(dist.values()) or 1
    print(f"\nScoreline backtest — {n} played group matches "
          f"across {len(configs)} tournaments")
    print(f"  actual results: home {dist['home'] / total * 100:.0f}%  "
          f"draw {dist['draw'] / total * 100:.0f}%  "
          f"away {dist['away'] / total * 100:.0f}%")
    print("  (pool scoring: exact score = 3 pts, correct result = 1 pt)\n")
    print(f"  {'system':<23}{'pts/match':>10}{'exact':>9}{'result':>9}")
    print("  " + "-" * 51)
    for system, d in sorted(agg.items(), key=lambda kv: -kv[1]["points"]):
        if not d["n"]:
            continue
        ppm = d["points"] / d["n"]
        exact = d["exact"] / d["n"] * 100
        res = (d["exact"] + d["toto"]) / d["n"] * 100
        tag = "  <- our model" if system.startswith("model") else ""
        print(f"  {system:<23}{ppm:>10.3f}{exact:>8.0f}%{res:>8.0f}%{tag}")
    print()


def print_predictions(config: dict, preds: list[dict], top: int | None) -> None:
    teams = config["teams"]

    def name(code: str) -> str:
        return teams[code]["name"]

    print(f"\n{config['name']}  —  most likely scorelines\n")
    width = max((len(name(p["home"])) for p in preds), default=10)
    earned = possible = 0
    grouped: dict[str, list[dict]] = {}
    for pr in preds:
        grouped.setdefault(pr["label"], []).append(pr)
    for label, items in grouped.items():
        print(f"\n  {label}")
        for pr in items:
            w, d, l = pr["probs"]
            ga, gb = pr["score"]
            flags = ("*" if pr["market"] else "") + ("!" if pr["blowout"] else "")
            line = (f"    {name(pr['home']):>{width}}  {ga}-{gb}  "
                    f"{name(pr['away']):<{width}}  "
                    f"(W {w * 100:.0f}% · D {d * 100:.0f}% · L {l * 100:.0f}%) {flags}")
            if pr["actual"] is not None:
                agoal, bgoal = pr["actual"]
                earned += pr["points"]
                possible += POOL_EXACT
                mark = {"exact": "OK exact", "toto": "OK result",
                        "miss": "X"}[pr["category"]]
                line += f"   actual {agoal}-{bgoal}  [{mark} +{pr['points']}]"
            print(line)
            if top and pr.get("top"):
                alt = "  ".join(f"{s[0]}-{s[1]} {p * 100:.0f}%" for s, p in pr["top"])
                print(f"    {'':>{width}}     -> {alt}")
    if possible:
        print(f"\n  pool score: {earned}/{possible} points "
              f"({earned / possible * 100:.0f}%)")
    if any(pr["market"] for pr in preds) or any(pr["blowout"] for pr in preds):
        print("\n  * market-blended   ! blowout tier")


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("path", type=Path,
                   help="tournament YAML (or a directory, with --backtest)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--top", type=int, metavar="N",
                   help="also show the N most likely scorelines per match")
    p.add_argument("--backtest", action="store_true",
                   help="score the predictor on played group matches vs baselines")
    p.add_argument("--outcome-aware", action="store_true",
                   help="condition each score on the likeliest result (fewer draws)")
    p.add_argument("--blowout", type=int, default=BLOWOUT_GAP_DEFAULT,
                   help="raw ELO gap for an outright 4-0 (0 disables)")
    p.add_argument("--market-weight", type=float, default=MARKET_WEIGHT_DEFAULT,
                   help="weight on market xG when blending (0..1)")
    p.add_argument("--no-market", action="store_true", help="ignore the odds block")
    p.add_argument("--seed", type=int, default=0,
                   help="seed for deterministic group-standings tiebreaks")
    args = p.parse_args()

    random.seed(args.seed)
    opts = dict(
        blowout=(args.blowout or None),
        market_weight=args.market_weight,
        use_market=not args.no_market,
    )

    if args.backtest:
        run_backtest(args.path, args.json, opts)
        return

    config = safe_load(args.path.read_text())
    preds = predict_tournament(
        config, top=args.top, outcome_aware=args.outcome_aware, **opts
    )
    if args.json:
        print(json.dumps(preds, default=str))
        return
    print_predictions(config, preds, args.top)


if __name__ == "__main__":
    main()
