# Football Predictions

Looking for the pre-2026 code? See [`legacy/`](legacy/).

This is a self-contained Django project for simulating international football
tournaments. It is rendered inside `douwe.com`, but the folder is also a
standalone Git repository so the data, simulator, and frontend can be checked
out independently.

The current version reads pre-tournament ratings from
[eloratings.net](https://www.eloratings.net/), runs Monte Carlo simulations of
group stages and knockout brackets, and lets you adjust the model directly in
the browser. Controls include ELO predictive strength, home advantage, recent
form, and late-round effects. The model can also be backtested against completed
World Cups and Euros.

## Layout

- `football_predictions.html` — project metadata, template, and page styles.
- `football_predictions.py` — Django project adapter and lazy backtest endpoint.
- `static/simulate.js` — browser/Node tournament simulator.
- `static/app.js` — frontend controls, rendering, and backtesting.
- `tournaments/` — YAML tournament definitions and historical results.
- `fetch_elo.py` — scraper/updater for ELO ratings and match schedules.
- `simulate.py` — Python simulator used by data tooling.
- `test_*.js` — focused Node tests for simulator/model behavior.

## Local Development

Run the parent Django site:

```bash
cd ../..
source venv3/bin/activate
uvicorn djangosite.asgi:application --reload --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000/projects/football_predictions
```

## Data Updates

Tournament YAML files contain:

- `elo` — rating at the tournament cutoff.
- `elo_9m` — rating roughly nine months before the tournament.
- actual group and knockout results, where available.

To refresh one tournament:

```bash
venv3/bin/python projects/football_predictions/fetch_elo.py \
  projects/football_predictions/tournaments/wc_2026.yaml
```

## Checks

From the parent `djangosite` directory:

```bash
node --check projects/football_predictions/static/app.js
node --check projects/football_predictions/static/simulate.js
node projects/football_predictions/test_model_settings.js
venv3/bin/python -m py_compile \
  projects/football_predictions/football_predictions.py \
  projects/football_predictions/fetch_elo.py
```
