import os
from copy import deepcopy
from datetime import date, timedelta

from projects.football_predictions.yaml_compat import safe_load as yaml_safe_load

from projects.common import JsonResponse, Project

TOURNAMENTS_DIR = os.path.join(os.path.dirname(__file__), "tournaments")


def _load_tournament_header(path):
    lines = []
    with open(path) as f:
        for line in f:
            if line.startswith(("teams:", "stages:")):
                break
            lines.append(line)
    return yaml_safe_load("".join(lines)) or {}


def _list_tournaments():
    out = []
    for fname in os.listdir(TOURNAMENTS_DIR):
        if not fname.endswith(".yaml"):
            continue
        slug = fname[:-5]
        data = _load_tournament_header(os.path.join(TOURNAMENTS_DIR, fname))
        year = data.get("year") or 0
        out.append((slug, data.get("name", slug), year))
    # Newest first; ties keep alpha order by name for stability.
    out.sort(key=lambda t: (-t[2], t[1]))
    return [(slug, name) for slug, name, _ in out]


def _load_tournament(slug):
    path = os.path.join(TOURNAMENTS_DIR, slug + ".yaml")
    if not os.path.isfile(path):
        return None
    return yaml_safe_load(open(path).read())


def _default_tournament_slug():
    tournaments = _list_tournaments()
    return tournaments[0][0] if tournaments else ""


def _select_tournament(slug=None):
    if not slug:
        slug = _default_tournament_slug()
    config = _load_tournament(slug) if slug else None
    if config is None and slug:
        slug = _default_tournament_slug()
        config = _load_tournament(slug) if slug else None
    return slug, config


def _jsonable(obj):
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    return obj


def _as_date(value):
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    return None


def _scheduled_dates(config):
    for stage in config.get("stages", []):
        if stage.get("type") == "groups":
            for entry in stage.get("schedule") or []:
                if entry:
                    day = _as_date(entry[0])
                    if day:
                        yield day
        elif stage.get("type") == "bracket":
            for match in (stage.get("matches") or {}).values():
                day = _as_date(match.get("date"))
                if day:
                    yield day
        elif stage.get("type") == "match":
            day = _as_date(stage.get("date"))
            if day:
                yield day


def _tournament_dates(config):
    start = _as_date(config.get("start")) or _as_date(config.get("cutoff"))
    end = _as_date(config.get("end"))
    dates = list(_scheduled_dates(config))
    if dates:
        start = min([start, *dates] if start else dates)
        end = max([end, *dates] if end else dates)
    if start and not end:
        end = start + timedelta(days=60)
    return start, end


def _is_active_tournament(config, today=None):
    today = today or date.today()
    start, end = _tournament_dates(config)
    return bool(start and end and start <= today <= end)


def _actual_overrides(config):
    out = {}
    for stage in config.get("stages", []):
        if stage.get("type") == "groups":
            for entry in stage.get("schedule") or []:
                if len(entry) >= 6:
                    _, _, home, away, hg, ag = entry[:6]
                    if hg is not None and ag is not None:
                        out[f"{stage['id']}.{home}.{away}"] = [hg, ag]
        elif stage.get("type") == "bracket":
            for mid, match in (stage.get("matches") or {}).items():
                actual = match.get("actual") or {}
                if actual.get("winner"):
                    o = {"winner": actual["winner"]}
                    if actual.get("score"):
                        o["score"] = actual["score"]
                    out[f"{stage['id']}.{mid}"] = o
        elif stage.get("type") == "match":
            actual = stage.get("actual") or {}
            if actual.get("winner"):
                o = {"winner": actual["winner"]}
                if actual.get("score"):
                    o["score"] = actual["score"]
                out[stage["id"]] = o
    return out


def _fetch_live_results(config):
    from projects.football_predictions.fetch_elo import (
        fetch_results,
        load_team_aliases,
        update_schedule,
    )

    match_code = config.get("match_code")
    year = config.get("year")
    if not match_code or not year:
        return config, []
    live_config = deepcopy(config)
    results = fetch_results(int(year), match_code, load_team_aliases())
    warnings = update_schedule(live_config, results) if results else []
    return live_config, warnings


def _completed_tournaments():
    out = []
    for slug, _ in _list_tournaments():
        config = _load_tournament(slug)
        if not config or config.get("year", 0) >= 2026:
            continue
        out.append(config)
    return out


class FootballPredictions(Project):
    def fill_dict(self, request, d):
        tournaments = _list_tournaments()
        slug, config = _select_tournament(request.GET.get("tournament"))
        d["tournaments"] = tournaments
        d["tournament_slug"] = slug
        d["tournament"] = _jsonable(config)

    def handle_request(self, handler, request):
        if handler == "backtest":
            return JsonResponse(_jsonable(_completed_tournaments()), safe=False)
        if handler == "live-results":
            slug, config = _select_tournament(request.GET.get("tournament"))
            if config is None:
                return JsonResponse({"error": "unknown tournament"}, status=404)
            if not _is_active_tournament(config):
                return JsonResponse({"active": False, "overrides": {}})
            try:
                live_config, warnings = _fetch_live_results(config)
            except Exception as e:
                return JsonResponse({"active": True, "error": str(e)}, status=502)
            return JsonResponse(
                _jsonable({
                    "active": True,
                    "overrides": _actual_overrides(live_config),
                    "warnings": warnings,
                })
            )
