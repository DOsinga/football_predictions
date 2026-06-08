import os
from datetime import date

from django.http import JsonResponse

from projects.football_predictions.yaml_compat import safe_load as yaml_safe_load

from projects.common import Project

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


def _jsonable(obj):
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    return obj


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
        slug = request.GET.get("tournament")
        config = _load_tournament(slug) if slug else None
        if not slug or config is None:
            slug = tournaments[0][0] if tournaments else ""
            config = _load_tournament(slug)
        d["tournaments"] = tournaments
        d["tournament_slug"] = slug
        d["tournament"] = _jsonable(config)

    def handle_request(self, handler, request):
        if handler == "backtest":
            return JsonResponse(_jsonable(_completed_tournaments()), safe=False)
