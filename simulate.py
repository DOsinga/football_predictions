"""Monte Carlo tournament simulator using ELO ratings.

Goal model: independent Poisson per team.
    xg_a = max(0.25, 1.35 + (elo_a - elo_b) * 0.005)
    xg_b = max(0.25, 1.35 - (elo_a - elo_b) * 0.005)

So a 200-ELO gap shifts expected GD by ~1 goal. Knockout draws go to extra
time (1/3 of regulation goal expectation) and then a near-coin-flip shootout
slightly weighted by ELO.
"""
from __future__ import annotations

import argparse
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml

BASE_XG = 1.35
ELO_TO_XG = 0.0025         # tuned to match per-match W/D/L on 51K historical
                            # matches (bucket calibration shows 0.003 was ~3-4pp
                            # over-confident at the favorite at moderate diffs)
MIN_XG = 0.25
ET_FRACTION = 1 / 3        # 30 of 90 minutes
PK_ELO_DAMP = 1200         # penalty shootouts: 1200-ELO scale (very dampened)
HOST_ADVANTAGE_DEFAULT = 50  # ELO bonus for host country teams


def effective_elos(config: dict) -> dict[str, int]:
    """Return {code: elo + host_bonus_if_host}."""
    hosts = set(config.get("hosts") or [])
    boost = config.get("host_advantage")
    if boost is None:
        boost = HOST_ADVANTAGE_DEFAULT
    return {
        code: info["elo"] + (boost if code in hosts else 0)
        for code, info in config["teams"].items()
    }


GLOBAL_CRITERIA = {"points", "gd", "gf"}
H2H_CRITERIA = {"h2h_points", "h2h_gd", "h2h_gf"}


@dataclass
class Match:
    home: str
    away: str
    home_goals: int
    away_goals: int


def poisson(mean: float) -> int:
    """Knuth's algorithm; fine for the small means we use."""
    L = math.exp(-mean)
    k, p = 0, 1.0
    while True:
        k += 1
        p *= random.random()
        if p <= L:
            return k - 1


def regulation_goals(elo_a: int, elo_b: int) -> tuple[int, int]:
    xg_a = max(MIN_XG, BASE_XG + (elo_a - elo_b) * ELO_TO_XG)
    xg_b = max(MIN_XG, BASE_XG - (elo_a - elo_b) * ELO_TO_XG)
    return poisson(xg_a), poisson(xg_b)


def knockout_winner(name_a: str, elo_a: int, name_b: str, elo_b: int) -> tuple[str, str]:
    """Returns (winner, loser). Tied regulations resolve by coin flip — stands
    in for extra time + penalty shootouts (empirically close to 50/50) and
    absorbs day-of-form variance the per-match model can't see."""
    ga, gb = regulation_goals(elo_a, elo_b)
    if ga != gb:
        return (name_a, name_b) if ga > gb else (name_b, name_a)
    return (name_a, name_b) if random.random() < 0.5 else (name_b, name_a)


def team_record(team: str, matches: list[Match]) -> dict[str, int]:
    pts = gf = ga = 0
    for m in matches:
        if m.home == team:
            gf += m.home_goals; ga += m.away_goals
            if m.home_goals > m.away_goals: pts += 3
            elif m.home_goals == m.away_goals: pts += 1
        elif m.away == team:
            gf += m.away_goals; ga += m.home_goals
            if m.away_goals > m.home_goals: pts += 3
            elif m.away_goals == m.home_goals: pts += 1
    return {"points": pts, "gd": gf - ga, "gf": gf}


def criterion_value(crit: str, team: str, matches: list[Match]) -> float:
    if crit == "random":
        return random.random()
    key = crit.replace("h2h_", "")
    return team_record(team, matches)[key]


def rank_group(teams: list[str], matches: list[Match], criteria: list[str]) -> list[str]:
    """Sort teams best-first by criteria. h2h_* criteria restrict matches to
    those among the currently-tied subgroup."""
    if len(teams) <= 1 or not criteria:
        return list(teams)
    head, *rest = criteria
    scope = matches
    if head in H2H_CRITERIA:
        team_set = set(teams)
        scope = [m for m in matches if m.home in team_set and m.away in team_set]
    keys = {t: criterion_value(head, t, scope) for t in teams}
    buckets: dict = defaultdict(list)
    for t in teams:
        buckets[keys[t]].append(t)
    out: list[str] = []
    for k in sorted(buckets.keys(), reverse=True):
        bucket = buckets[k]
        out.extend(bucket if len(bucket) == 1 else rank_group(bucket, matches, rest))
    return out


def resolve(ref: str, slots: dict[str, str], teams: dict) -> str:
    if ref in teams:
        return ref
    if ref in slots:
        return slots[ref]
    raise KeyError(f"unresolved slot reference: {ref}")


def teams_in_stage(stage: dict, slots: dict, teams: dict) -> set[str]:
    t = set()
    if stage["type"] == "groups":
        for grp in stage["groups"].values():
            t.update(resolve(x, slots, teams) for x in grp)
    elif stage["type"] == "bracket":
        for m in stage["matches"].values():
            t.add(resolve(m["home"], slots, teams))
            t.add(resolve(m["away"], slots, teams))
    elif stage["type"] == "match":
        t.add(resolve(stage["home"], slots, teams))
        t.add(resolve(stage["away"], slots, teams))
    # thirds_pool stages don't introduce new teams; they redirect existing ones.
    return t


def thirds_assign(stage: dict, slots: dict, team_stats: dict, source_stage: dict) -> None:
    """Rank source_stage's Nth-place teams across groups (points → GD → GF →
    random) and fill stage.id slots either by the per-combo assignment table
    or, if no entry matches, by raw rank (slot names 1..take)."""
    candidates = []
    for group_name in source_stage["groups"]:
        team = slots.get(f"{stage['source']}.{group_name}.{stage['rank']}")
        if not team:
            continue
        stats = team_stats.get(team, {"points": 0, "gd": 0, "gf": 0})
        candidates.append({"team": team, "group": group_name, **stats})
    candidates.sort(
        key=lambda c: (-c["points"], -c["gd"], -c["gf"], random.random())
    )
    top = candidates[: stage["take"]]
    combo = "".join(sorted(c["group"] for c in top))
    assignment = (stage.get("assignments") or {}).get(combo)
    if assignment:
        for slot_name, source_group in assignment.items():
            c = next((x for x in candidates if x["group"] == source_group), None)
            if c:
                slots[f"{stage['id']}.{slot_name}"] = c["team"]
    else:
        for i, c in enumerate(top, start=1):
            slots[f"{stage['id']}.{i}"] = c["team"]


def simulate_once(config: dict) -> tuple[dict[str, str], dict[str, set[str]]]:
    teams = config["teams"]
    criteria = config.get("tiebreakers",
                          ["points", "gd", "gf", "h2h_points", "h2h_gd", "random"])
    slots: dict[str, str] = {}
    reached: dict[str, set[str]] = {}
    team_stats: dict[str, dict] = {}
    elo = effective_elos(config)

    for stage in config["stages"]:
        sid = stage["id"]
        reached[sid] = teams_in_stage(stage, slots, teams)

        if stage["type"] == "groups":
            pairings = stage.get("pairings") or {}
            for group_name, codes in stage["groups"].items():
                actual = [resolve(c, slots, teams) for c in codes]
                if group_name in pairings:
                    pairs = [
                        (resolve(a, slots, teams), resolve(b, slots, teams))
                        for a, b in pairings[group_name]
                    ]
                else:
                    pairs = [
                        (actual[i], actual[j])
                        for i in range(len(actual))
                        for j in range(i + 1, len(actual))
                    ]
                matches: list[Match] = []
                for a, b in pairs:
                    ga, gb = regulation_goals(elo[a], elo[b])
                    matches.append(Match(a, b, ga, gb))
                for code in actual:
                    rec = team_record(code, matches)
                    team_stats[code] = {**rec, "group": group_name}
                ranked = rank_group(actual, matches, criteria)
                for pos, code in enumerate(ranked, start=1):
                    slots[f"{sid}.{group_name}.{pos}"] = code

        elif stage["type"] == "thirds_pool":
            source = next((s for s in config["stages"] if s["id"] == stage["source"]), None)
            if source is None:
                raise KeyError(f"thirds_pool: source stage {stage['source']!r} not found")
            thirds_assign(stage, slots, team_stats, source)

        elif stage["type"] == "bracket":
            for m_id, m_def in stage["matches"].items():
                a = resolve(m_def["home"], slots, teams)
                b = resolve(m_def["away"], slots, teams)
                w, l = knockout_winner(a, elo[a], b, elo[b])
                slots[f"{sid}.{m_id}.W"] = w
                slots[f"{sid}.{m_id}.L"] = l

        elif stage["type"] == "match":
            a = resolve(stage["home"], slots, teams)
            b = resolve(stage["away"], slots, teams)
            w, l = knockout_winner(a, elo[a], b, elo[b])
            slots[f"{sid}.W"] = w
            slots[f"{sid}.L"] = l

    return slots, reached


def simulate(config: dict, n: int) -> dict[str, Counter]:
    """Returns {team_code: Counter({stage_id: count, 'champion': count})}."""
    stage_ids = [s["id"] for s in config["stages"]]
    champion_slot = config.get("champion_slot") or f"{stage_ids[-1]}.W"
    stats: dict[str, Counter] = defaultdict(Counter)

    for _ in range(n):
        slots, reached = simulate_once(config)
        for sid, teams_set in reached.items():
            for t in teams_set:
                stats[t][sid] += 1
        if champion_slot in slots:
            stats[slots[champion_slot]]["champion"] += 1
    return stats


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("yaml_file", type=Path)
    p.add_argument("--n", type=int, default=10000)
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    config = yaml.safe_load(args.yaml_file.read_text())
    stage_ids = [s["id"] for s in config["stages"]]
    stats = simulate(config, args.n)

    print(f"\n{config['name']}  —  {args.n} simulations\n")
    # Skip the group stage (everyone reaches) and any third_place stage (noisy).
    cols = [s for s in stage_ids[1:] if s != "third_place"] + ["champion"]
    width = max(len(c) for c in cols) + 2
    header = f"  {'Team':<28}" + "".join(f"{c:>{width}}" for c in cols)
    print(header)
    print("  " + "-" * (len(header) - 2))

    by_champion = sorted(
        config["teams"].items(),
        key=lambda kv: -stats[kv[0]]["champion"],
    )
    for code, info in by_champion:
        cells = "".join(f"{stats[code][c] / args.n * 100:>{width - 1}.1f}%" for c in cols)
        print(f"  {info['name']:<28}{cells}")


if __name__ == "__main__":
    main()
