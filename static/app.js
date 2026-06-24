(function () {
  const config = JSON.parse(document.getElementById('fp-data').textContent);
  const resultsEl = document.getElementById('fp-results');
  const matchesEl = document.getElementById('fp-matches');
  const statusEl = document.getElementById('fp-status');
  const statusFillEl = document.getElementById('fp-status-fill');
  const statusTextEl = document.getElementById('fp-status-text');
  const tournamentSelect = document.getElementById('fp-tournament');
  const runBtn = document.getElementById('fp-run');
  const loadBtn = document.getElementById('fp-load-actual');
  const resetBtn = document.getElementById('fp-reset');
  const modelResetBtn = document.getElementById('fp-model-reset');
  const curveEl = document.getElementById('fp-curve');
  const eloStrengthInput = document.getElementById('fp-elo-strength-input');
  const hostAdvantageInput = document.getElementById('fp-host-advantage-input');
  const recencyInput = document.getElementById('fp-recency-input');
  const laterRoundsInput = document.getElementById('fp-later-rounds-input');
  const backtestBtn = document.getElementById('fp-backtest-run');
  const backtestFillEl = document.getElementById('fp-backtest-fill');
  const backtestStatusEl = document.getElementById('fp-backtest-status');
  const backtestResultsEl = document.getElementById('fp-backtest-results');

  const N_SIMS = 10000;
  const BACKTEST_SIMS = 5000;
  const DEFAULT_BACKTEST_BASELINE = {
    n: 39,
    top1: 9,
    top4: 30,
    semifinalOverlap: 2.03,
    brier: 0.1245,
  };
  const TOP_N = 10;
  const LS_KEY = `fp-overrides:${config.name}`;
  const MODEL_LS_KEY = `fp-model:${config.name}`;

  let expanded = false;
  let latestStats = null;
  let latestNSims = N_SIMS;
  let runGeneration = 0;
  let backtestGeneration = 0;
  let backtestConfigsPromise = null;

  const FLAG_ALIASES = {
    DD: 'DE',  // East Germany
    EI: 'GB',  // Northern Ireland has no official emoji flag.
    EN: 'GB-ENG',
    ID: 'ID',  // Dutch East Indies
    NM: 'MK',  // North Macedonia
    SQ: 'GB-SCT',
    WA: 'GB-WLS',
  };

  const BACKTEST_HELP = {
    top1: 'Counts tournaments where the team with the highest predicted champion probability actually won.',
    top4: 'Counts tournaments where the actual champion was among the four highest predicted champion probabilities.',
    semifinalOverlap: 'Average overlap between the four teams most likely to reach the semi-finals and the actual semi-finalists.',
    brier: 'Average squared error for reaching each later stage and winning the tournament. Lower is better.',
  };

  // -------- Overrides storage --------

  function loadOverrides() {
    try { return JSON.parse(localStorage.getItem(LS_KEY) || '{}'); } catch (e) { return {}; }
  }
  function saveOverrides(ovr) {
    try { localStorage.setItem(LS_KEY, JSON.stringify(ovr)); } catch (e) {}
  }
  function completeOverrides(ovr) {
    const out = {};
    for (const [key, value] of Object.entries(ovr || {})) {
      if (Array.isArray(value)) {
        const [h, a] = value;
        if (h !== '' && a !== '' && h != null && a != null) out[key] = [h, a];
        continue;
      }
      if (!value || !value.winner) continue;
      const fixed = { winner: value.winner };
      if (value.score && value.score[0] !== '' && value.score[1] !== '') {
        fixed.score = value.score;
      }
      out[key] = fixed;
    }
    return out;
  }
  function hasUserValue(value) {
    if (Array.isArray(value)) return value.some(v => v !== '' && v != null);
    if (!value) return false;
    if (value.winner) return true;
    return Boolean(value.score && value.score.some(v => v !== '' && v != null));
  }
  function sameOverride(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }
  function mergeOfficialOverrides(current, official) {
    const out = { ...(current || {}) };
    let changed = false;
    for (const [key, value] of Object.entries(official || {})) {
      if (hasUserValue(out[key])) continue;
      if (sameOverride(out[key], value)) continue;
      out[key] = value;
      changed = true;
    }
    return { overrides: out, changed };
  }
  function loadModel() {
    try {
      const saved = JSON.parse(localStorage.getItem(MODEL_LS_KEY) || '{}');
      // Older builds allowed recency from 0–200%. Keep valid centered values,
      // but reset old out-of-range experiments to the new neutral midpoint.
      if (saved.recency < -0.5 || saved.recency > 0.5) saved.recency = 0;
      return { ...Football.DEFAULT_MODEL, ...saved };
    } catch (e) {
      return { ...Football.DEFAULT_MODEL };
    }
  }
  function saveModel(model) {
    try { localStorage.setItem(MODEL_LS_KEY, JSON.stringify(model)); } catch (e) {}
  }

  let model = loadModel();

  // -------- Display helpers --------

  function flagFromIso(code) {
    if (code === 'GB-ENG') return '\uD83C\uDFF4\uDB40\uDC67\uDB40\uDC62\uDB40\uDC65\uDB40\uDC6E\uDB40\uDC67\uDB40\uDC7F';
    if (code === 'GB-SCT') return '\uD83C\uDFF4\uDB40\uDC67\uDB40\uDC62\uDB40\uDC73\uDB40\uDC63\uDB40\uDC74\uDB40\uDC7F';
    if (code === 'GB-WLS') return '\uD83C\uDFF4\uDB40\uDC67\uDB40\uDC62\uDB40\uDC77\uDB40\uDC6C\uDB40\uDC73\uDB40\uDC7F';
    if (!/^[A-Z]{2}$/.test(code)) return '';
    return String.fromCodePoint(...[...code].map(c => 127397 + c.charCodeAt(0)));
  }

  function flagForCode(code) {
    return flagFromIso(FLAG_ALIASES[code] || code);
  }

  function teamLabel(tournament, code) {
    if (!code) return 'TBD';
    const name = tournament.teams[code]?.name || code;
    const flag = flagForCode(code);
    return `${flag ? `<span class="fp-flag" aria-hidden="true">${flag}</span>` : ''}${name}`;
  }

  function teamText(tournament, code) {
    if (!code) return 'TBD';
    const name = tournament.teams[code]?.name || code;
    const flag = flagForCode(code);
    return `${flag ? `${flag} ` : ''}${name}`;
  }

  function infoIcon(text) {
    return `<span class="fp-info" title="${text}" aria-label="${text}" tabindex="0">i</span>`;
  }

  // -------- Model controls --------

  function curvePath(key, points, x, y) {
    return points.map((p, i) => `${i ? 'L' : 'M'}${x(p.diff).toFixed(1)},${y(p[key]).toFixed(1)}`).join(' ');
  }

  function curveBandPath(lowerKey, upperKey, points, x, y) {
    const upper = points.map((p, i) =>
      `${i ? 'L' : 'M'}${x(p.diff).toFixed(1)},${y(p[upperKey]).toFixed(1)}`).join(' ');
    const lower = [...points].reverse().map(p =>
      `L${x(p.diff).toFixed(1)},${y(p[lowerKey]).toFixed(1)}`).join(' ');
    return `${upper}${lower}Z`;
  }

  function renderModelControls() {
    const hasRecencyData = Object.values(config.teams).some(info => info.elo_9m != null);
    eloStrengthInput.value = model.eloStrength;
    hostAdvantageInput.value = model.hostAdvantage;
    recencyInput.value = model.recency;
    recencyInput.disabled = !hasRecencyData;
    laterRoundsInput.value = model.laterRoundEffect;
    document.getElementById('fp-elo-strength').value = `${Math.round(model.eloStrength * 100)}%`;
    document.getElementById('fp-host-advantage').value = `+${model.hostAdvantage} ELO`;
    document.getElementById('fp-recency').value =
      !hasRecencyData ? 'Unavailable' :
      model.recency === 0 ? 'None' :
      `${model.recency > 0 ? '+' : ''}${Math.round(model.recency * 100)}%`;
    document.getElementById('fp-later-rounds').value =
      model.laterRoundEffect === 0 ? 'None' :
      model.laterRoundEffect === 1 ? 'Current' :
      `${Math.round(model.laterRoundEffect * 100)}%`;

    const points = [];
    for (let diff = 0; diff <= 500; diff += 10) {
      const outcome = Football.outcomeProbabilities(diff, model);
      points.push({
        diff,
        bottom: 0,
        loss: outcome.loss,
        lossDraw: outcome.loss + outcome.draw,
        top: 1,
      });
    }
    const left = 38, right = 508, top = 12, bottom = 215;
    const x = diff => left + diff / 500 * (right - left);
    const y = p => bottom - p * (bottom - top);
    const grid = [0, .25, .5, .75, 1].map(p =>
      `<line class="grid" x1="${left}" y1="${y(p)}" x2="${right}" y2="${y(p)}"></line>` +
      `<text x="${left - 7}" y="${y(p) + 4}" text-anchor="end">${Math.round(p * 100)}%</text>`).join('');
    const ticks = [0, 100, 200, 300, 400, 500].map(diff =>
      `<line class="axis" x1="${x(diff)}" y1="${bottom}" x2="${x(diff)}" y2="${bottom + 4}"></line>` +
      `<text x="${x(diff)}" y="${bottom + 18}" text-anchor="middle">${diff}</text>`).join('');
    curveEl.innerHTML =
      `<path class="win" d="${curveBandPath('lossDraw', 'top', points, x, y)}"></path>` +
      `<path class="draw" d="${curveBandPath('loss', 'lossDraw', points, x, y)}"></path>` +
      `<path class="loss" d="${curveBandPath('bottom', 'loss', points, x, y)}"></path>` +
      grid + `<line class="axis" x1="${left}" y1="${bottom}" x2="${right}" y2="${bottom}"></line>` +
      ticks +
      `<path class="boundary" d="${curvePath('lossDraw', points, x, y)}"></path>` +
      `<path class="boundary" d="${curvePath('loss', points, x, y)}"></path>`;
  }

  function readModelControls() {
    model = {
      eloStrength: parseFloat(eloStrengthInput.value),
      hostAdvantage: parseInt(hostAdvantageInput.value, 10),
      recency: parseFloat(recencyInput.value),
      laterRoundEffect: parseFloat(laterRoundsInput.value),
    };
    saveModel(model);
    renderModelControls();
    refreshBacktestUi({ clearChangedResults: true });
  }

  // Pre-fill overrides from the YAML's actual results.
  function actualOverridesFor(tournament) {
    const out = {};
    for (const stage of tournament.stages) {
      if (stage.type === 'groups' && stage.schedule) {
        for (const entry of stage.schedule) {
          const [, , home, away, hg, ag] = entry;
          if (hg != null && ag != null) {
            out[`${stage.id}.${home}.${away}`] = [hg, ag];
          }
        }
      } else if (stage.type === 'bracket') {
        for (const [mid, mdef] of Object.entries(stage.matches)) {
          if (mdef.actual && mdef.actual.winner) {
            const o = { winner: mdef.actual.winner };
            if (mdef.actual.score) o.score = mdef.actual.score;
            out[`${stage.id}.${mid}`] = o;
          }
        }
      } else if (stage.type === 'match') {
        if (stage.actual && stage.actual.winner) {
          const o = { winner: stage.actual.winner };
          if (stage.actual.score) o.score = stage.actual.score;
          out[stage.id] = o;
        }
      }
    }
    return out;
  }

  function actualOverrides() {
    return actualOverridesFor(config);
  }

  // -------- Backtest --------

  function actualTournament(tournament) {
    const overrides = actualOverridesFor(tournament);
    const actual = Football.simulateOnce(tournament, overrides, Football.DEFAULT_MODEL);
    const stageIds = tournament.stages.map(s => s.id);
    const championSlot = tournament.champion_slot || `${stageIds[stageIds.length - 1]}.W`;
    return { reached: actual.reached, champion: actual.slots.get(championSlot) };
  }

  function probability(stats, team, key, n) {
    return ((stats.get(team) || new Map()).get(key) || 0) / n;
  }

  function scoreTournament(tournament, stats, actual, n) {
    const teams = Object.keys(tournament.teams);
    const ranked = [...teams].sort((a, b) =>
      probability(stats, b, 'champion', n) - probability(stats, a, 'champion', n));
    const top1 = ranked[0] === actual.champion ? 1 : 0;
    const top4 = ranked.slice(0, 4).includes(actual.champion) ? 1 : 0;

    let semifinalOverlap = null;
    const sfActual = actual.reached.get('sf');
    if (sfActual && sfActual.size === 4 && tournament.stages[0].id !== 'sf') {
      const predicted = [...teams].sort((a, b) =>
        probability(stats, b, 'sf', n) - probability(stats, a, 'sf', n)).slice(0, 4);
      semifinalOverlap = predicted.filter(t => sfActual.has(t)).length;
    }

    const scoreKeys = tournament.stages.slice(1)
      .filter(s => s.type !== 'thirds_pool' && s.id !== 'third_place')
      .map(s => s.id);
    scoreKeys.push('champion');
    let brier = 0, observations = 0;
    for (const team of teams) {
      for (const key of scoreKeys) {
        const actualValue = key === 'champion'
          ? team === actual.champion
          : (actual.reached.get(key) || new Set()).has(team);
        const error = probability(stats, team, key, n) - Number(actualValue);
        brier += error * error;
        observations += 1;
      }
    }
    return {
      top1, top4, semifinalOverlap, brier: brier / observations,
      favorite: ranked[0], champion: actual.champion,
    };
  }

  function aggregateBacktest(rows, key) {
    const scores = rows.map(r => r.score[key]);
    if (key === 'brier') return scores.reduce((a, b) => a + b, 0) / scores.length;
    if (key === 'semifinalOverlap') {
      const available = scores.filter(x => x != null);
      return available.reduce((a, b) => a + b, 0) / available.length;
    }
    return scores.reduce((a, b) => a + b, 0);
  }

  function modelIsDefault() {
    return ['eloStrength', 'hostAdvantage', 'recency', 'laterRoundEffect']
      .every(key => model[key] === Football.DEFAULT_MODEL[key]);
  }

  function backtestMetricRows(customRows = null) {
    const n = customRows ? customRows.length : DEFAULT_BACKTEST_BASELINE.n;
    const metrics = [
      ['Top 1 champions', 'top1', v => `${v} / ${n}`, false, BACKTEST_HELP.top1],
      ['Top 4 champions', 'top4', v => `${v} / ${n}`, false, BACKTEST_HELP.top4],
      ['Semifinalist overlap', 'semifinalOverlap', v => `${v.toFixed(2)} / 4`, false, BACKTEST_HELP.semifinalOverlap],
      ['Stage Brier', 'brier', v => v.toFixed(4), true, BACKTEST_HELP.brier],
    ];
    return metrics.map(([label, key, fmt, lower, help]) => {
      const baseline = DEFAULT_BACKTEST_BASELINE[key];
      if (!customRows) return `<tr><td>${label}${infoIcon(help)}</td><td>${fmt(baseline)}</td></tr>`;
      const yours = aggregateBacktest(customRows, key);
      const yoursBetter = lower ? yours < baseline : yours > baseline;
      const defaultBetter = lower ? baseline < yours : baseline > yours;
      return `<tr><td>${label}${infoIcon(help)}</td><td class="${yoursBetter ? 'better' : ''}">${fmt(yours)}</td>` +
        `<td class="${defaultBetter ? 'better' : ''}">${fmt(baseline)}</td></tr>`;
    }).join('');
  }

  function renderDefaultBacktestBaseline() {
    backtestResultsEl.innerHTML =
      `<table><thead><tr><th>Measure</th><th>Default</th></tr></thead>` +
      `<tbody>${backtestMetricRows()}</tbody></table>`;
  }

  function refreshBacktestUi({ clearChangedResults = false } = {}) {
    const atDefault = modelIsDefault();
    backtestBtn.hidden = atDefault;
    backtestFillEl.style.width = '0';
    if (atDefault) {
      backtestGeneration += 1;
      backtestBtn.disabled = false;
      backtestStatusEl.textContent = 'Change model settings to run a backtest';
      renderDefaultBacktestBaseline();
    } else if (clearChangedResults) {
      backtestResultsEl.innerHTML = '';
      backtestStatusEl.textContent =
        `Ready to compare · ${BACKTEST_SIMS.toLocaleString()} simulations per tournament`;
    } else if (!backtestResultsEl.innerHTML) {
      backtestStatusEl.textContent =
        `Ready to compare · ${BACKTEST_SIMS.toLocaleString()} simulations per tournament`;
    }
  }

  function renderBacktest(customRows, elapsed) {
    const tournamentRows = customRows.map(row => {
      return `<tr><td>${row.tournament.name}</td><td>${teamLabel(row.tournament, row.score.champion)}</td>` +
        `<td>${teamLabel(row.tournament, row.score.favorite)}</td>` +
        `<td>${row.score.top4 ? 'Yes' : 'No'}</td><td>${row.score.brier.toFixed(4)}</td></tr>`;
    }).join('');
    backtestResultsEl.innerHTML =
      `<table><thead><tr><th>Measure</th><th>Your model</th><th>Default</th></tr></thead>` +
      `<tbody>${backtestMetricRows(customRows)}</tbody></table>` +
      `<div class="fp-backtest-tournaments"><table><thead><tr><th>Tournament</th><th>Champion</th>` +
      `<th>Your favorite</th><th>In your top 4</th><th>Brier</th></tr></thead>` +
      `<tbody>${tournamentRows}</tbody></table></div>`;
    backtestStatusEl.textContent = `${customRows.length} tournaments · ${(elapsed / 1000).toFixed(1)} s`;
    backtestFillEl.style.width = '0';
  }

  function loadBacktestConfigs() {
    if (!backtestConfigsPromise) {
      const url = new URL('/projects/football_predictions/backtest', window.location.origin);
      backtestConfigsPromise = fetch(url)
        .then(response => {
          if (!response.ok) throw new Error(`Backtest data request failed: ${response.status}`);
          return response.json();
        });
    }
    return backtestConfigsPromise;
  }

  function loadLiveResults() {
    const url = new URL('/projects/football_predictions/live-results', window.location.origin);
    const selected = new URL(window.location.href).searchParams.get('tournament');
    if (selected) url.searchParams.set('tournament', selected);
    return fetch(url)
      .then(response => {
        if (!response.ok) throw new Error(`Live results request failed: ${response.status}`);
        return response.json();
      })
      .then(data => {
        if (!data.active || data.error || !data.overrides) return false;
        const { overrides, changed } = mergeOfficialOverrides(loadOverrides(), data.overrides);
        if (!changed) return false;
        saveOverrides(overrides);
        renderMatches();
        run();
        return true;
      })
      .catch(() => false);
  }

  function runBacktest() {
    if (modelIsDefault()) return;
    const generation = ++backtestGeneration;
    backtestBtn.disabled = true;
    backtestResultsEl.innerHTML = '';
    backtestStatusEl.textContent = 'Loading historical tournaments…';
    backtestFillEl.style.width = '0';
    loadBacktestConfigs().then(backtestConfigs => {
      if (generation !== backtestGeneration) return;
      const jobs = [];
      for (const tournament of backtestConfigs) {
        const actual = actualTournament(tournament);
        jobs.push({ tournament, actual, settings: model, stats: new Map(), completed: 0, kind: 'custom' });
      }
      const totalSims = jobs.length * BACKTEST_SIMS;
      let totalCompleted = 0, jobIndex = 0;
      const started = performance.now();
      const chunk = () => {
        if (generation !== backtestGeneration) return;
        const chunkStart = performance.now();
        while (jobIndex < jobs.length && performance.now() - chunkStart < 80) {
          const job = jobs[jobIndex];
          const batch = Math.min(250, BACKTEST_SIMS - job.completed);
          Football.simulateInto(job.tournament, batch, null, job.settings, job.stats);
          job.completed += batch;
          totalCompleted += batch;
          if (job.completed === BACKTEST_SIMS) jobIndex += 1;
        }
        backtestStatusEl.textContent =
          `${totalCompleted.toLocaleString()} / ${totalSims.toLocaleString()} simulations`;
        backtestFillEl.style.width = `${totalCompleted / totalSims * 100}%`;
        if (jobIndex < jobs.length) {
          setTimeout(chunk, 0);
          return;
        }
        const customRows = jobs.map(j => ({
          tournament: j.tournament,
          score: scoreTournament(j.tournament, j.stats, j.actual, BACKTEST_SIMS),
        }));
        renderBacktest(customRows, performance.now() - started);
        backtestBtn.disabled = false;
      };
      setTimeout(chunk, 10);
    }).catch(err => {
      backtestStatusEl.textContent = 'Could not load backtest data';
      backtestResultsEl.innerHTML = `<p>${err.message}</p>`;
      backtestBtn.disabled = false;
    });
  }

  // -------- Results table --------

  function heatClass(p) {
    if (p < 1) return 'heat-0';
    if (p < 10) return 'heat-1';
    if (p < 25) return 'heat-2';
    if (p < 50) return 'heat-3';
    if (p < 75) return 'heat-4';
    return 'heat-5';
  }

  function resultRows(stats, nSims = N_SIMS) {
    // Skip the first stage (everyone reaches it), thirds_pool stages (no
    // team "reaches" — it's a derivation), and the 3rd-place playoff (noisy).
    const cols = config.stages
      .slice(1)
      .filter(s => s.type !== 'thirds_pool' && s.id !== 'third_place')
      .map(s => s.id);
    cols.push('champion');
    return { cols, rows: Object.entries(config.teams).map(([code, info]) => ({
      code, name: info.name, elo: info.elo,
      pcts: cols.map(c => ((stats && stats.get(code) || new Map()).get(c) || 0) / nSims * 100),
    })).sort((a, b) => {
      // Walk pcts right-to-left: champion %, then final %, then SF…
      // Eliminated teams (0% to win) get ranked by furthest stage reached.
      // No-simulation case (all zeros) falls through to ELO.
      for (let i = a.pcts.length - 1; i >= 0; i--) {
        if (a.pcts[i] !== b.pcts[i]) return b.pcts[i] - a.pcts[i];
      }
      return b.elo - a.elo;
    }) };
  }

  function fmtPct(p) {
    // Don't let near-edge MC noise round to 100% or 0% if it isn't exactly so.
    if (p === 100 || p === 0) return p.toFixed(1) + '%';
    if (p > 99 || p < 1) return p.toFixed(2) + '%';
    return p.toFixed(1) + '%';
  }

  function renderResults(stats, nSims = N_SIMS) {
    latestStats = stats;
    latestNSims = nSims;
    const { cols, rows } = resultRows(stats, nSims);
    const colLabel = { r16: 'R16', r32: 'R32', r64: 'R64', qf: 'QF', sf: 'SF',
                       final: 'Final', champion: 'Win' };
    const head = `<th>Team</th><th>ELO</th>` +
                 cols.map(c => `<th>${colLabel[c] || c}</th>`).join('');
    const showAll = expanded || rows.length <= TOP_N;
    const bodyRows = rows.map((r, i) => {
      const hide = !showAll && i >= TOP_N;
      return `<tr class="${hide ? 'hidden' : ''}">` +
        `<td>${teamLabel(config, r.code)}</td>` +
        `<td><input class="elo-input" type="number" data-code="${r.code}" value="${r.elo}" step="10"></td>` +
        r.pcts.map(p => `<td class="pct ${heatClass(p)}">${fmtPct(p)}</td>`).join('') +
        `</tr>`;
    });
    if (!showAll && rows.length > TOP_N) {
      bodyRows.splice(TOP_N, 0,
        `<tr class="expand" id="fp-expand"><td colspan="${2 + cols.length}">▼ Show all ${rows.length} teams</td></tr>`);
    } else if (showAll && rows.length > TOP_N) {
      bodyRows.push(
        `<tr class="expand" id="fp-expand"><td colspan="${2 + cols.length}">▲ Show top ${TOP_N}</td></tr>`);
    }
    resultsEl.innerHTML =
      `<table><thead><tr>${head}</tr></thead><tbody>${bodyRows.join('')}</tbody></table>`;
  }

  function readElos() {
    resultsEl.querySelectorAll('input.elo-input').forEach(inp => {
      const v = parseInt(inp.value, 10);
      if (!Number.isNaN(v)) config.teams[inp.dataset.code].elo = v;
    });
  }

  // -------- Matches feed --------

  const ROUND_LABEL = {
    r16: 'Round of 16', r32: 'Round of 32', r64: 'Round of 64',
    qf: 'Quarter-finals', sf: 'Semi-finals',
    third_place: '3rd-place playoff', final: 'Final',
  };

  function renderGroupMatch(stageId, group, home, away, overrides) {
    const key = `${stageId}.${home}.${away}`;
    const v = overrides[key] || ['', ''];
    const fixed = v[0] !== '' && v[1] !== '' ? ' fixed' : '';
    return `<div class="fp-match${fixed}" data-key="${key}" data-kind="group">` +
      `<span class="home">${teamLabel(config, home)}</span>` +
      `<input type="number" min="0" max="20" data-side="h" value="${v[0]}">` +
      `<span>–</span>` +
      `<input type="number" min="0" max="20" data-side="a" value="${v[1]}">` +
      `<span class="away">${teamLabel(config, away)}</span>` +
      `</div>`;
  }

  function renderKnockoutMatch(key, homeCode, awayCode, overrides) {
    const ovr = overrides[key] || {};
    const homeName = homeCode ? config.teams[homeCode].name : 'TBD';
    const awayName = awayCode ? config.teams[awayCode].name : 'TBD';
    if (!homeCode || !awayCode) {
      return `<div class="fp-match" data-key="${key}" data-kind="ko">` +
        `<span class="home">${homeCode ? teamLabel(config, homeCode) : homeName}</span>` +
        `<span class="vs">vs</span>` +
        `<span class="away">${awayCode ? teamLabel(config, awayCode) : awayName}</span>` +
        `<span class="tbd">pending</span>` +
        `</div>`;
    }
    // If the stored override's winner isn't one of the now-resolved
    // participants (e.g. group standings flipped and changed who's here),
    // treat the override as absent — clear inputs and let the simulator
    // play this match out.
    const winnerInPlay = ovr.winner && (ovr.winner === homeCode || ovr.winner === awayCode);
    const score = (!ovr.winner || winnerInPlay) && ovr.score ? ovr.score : ['', ''];
    const [hVal, aVal] = score;
    const bothFilled = hVal !== '' && aVal !== '';
    const equal = bothFilled && hVal === aVal;
    const winner = winnerInPlay ? ovr.winner : undefined;
    const showPk = equal;
    const fixed = ((bothFilled && !equal) || winner) ? ' fixed' : '';
    const sel = (v) => v === winner ? ' selected' : '';
    return `<div class="fp-match${fixed}" data-key="${key}" data-kind="ko" data-home="${homeCode}" data-away="${awayCode}">` +
      `<span class="home">${teamLabel(config, homeCode)}</span>` +
      `<input type="number" min="0" max="20" data-side="h" value="${hVal}">` +
      `<span>–</span>` +
      `<input type="number" min="0" max="20" data-side="a" value="${aVal}">` +
      `<span class="away">${teamLabel(config, awayCode)}</span>` +
      `<select data-pick="winner" class="pk-pick"${showPk ? '' : ' hidden'}>` +
        `<option value="">won by…</option>` +
        `<option value="${homeCode}"${sel(homeCode)}>${teamText(config, homeCode)}</option>` +
        `<option value="${awayCode}"${sel(awayCode)}>${teamText(config, awayCode)}</option>` +
      `</select>` +
      `</div>`;
  }

  function renderMatches() {
    const overrides = loadOverrides();
    const slots = Football.resolveDeterministic(config, completeOverrides(overrides));
    const parts = [];

    const section = (title, matchHtmls, extra = '') =>
      `<h4 class="fp-section">${title}</h4><div class="fp-grid ${extra}">${matchHtmls.join('')}</div>`;

    for (const stage of config.stages) {
      if (stage.type === 'groups') {
        const groupOrder = Object.keys(stage.groups);
        const matchesByGroup = Object.fromEntries(groupOrder.map(g => [g, []]));
        if (stage.schedule) {
          for (const entry of stage.schedule) {
            const [, group, home, away] = entry;
            matchesByGroup[group].push([home, away]);
          }
        } else {
          for (const [g, codes] of Object.entries(stage.groups)) {
            for (let i = 0; i < codes.length; i++)
              for (let j = i + 1; j < codes.length; j++)
                matchesByGroup[g].push([codes[i], codes[j]]);
          }
        }
        for (const g of groupOrder) {
          const matches = matchesByGroup[g].map(([h, a]) =>
            renderGroupMatch(stage.id, g, h, a, overrides));
          parts.push(section(`Group ${g}`, matches, 'fp-grid-3'));
        }
      } else if (stage.type === 'bracket') {
        const matches = Object.entries(stage.matches).map(([mid, mdef]) => {
          const homeCode = (mdef.home in config.teams) ? mdef.home : slots.get(mdef.home);
          const awayCode = (mdef.away in config.teams) ? mdef.away : slots.get(mdef.away);
          return renderKnockoutMatch(`${stage.id}.${mid}`, homeCode, awayCode, overrides);
        });
        parts.push(section(ROUND_LABEL[stage.id] || stage.id, matches, 'fp-grid-2'));
      } else if (stage.type === 'match') {
        const homeCode = (stage.home in config.teams) ? stage.home : slots.get(stage.home);
        const awayCode = (stage.away in config.teams) ? stage.away : slots.get(stage.away);
        parts.push(section(ROUND_LABEL[stage.id] || stage.id,
          [renderKnockoutMatch(stage.id, homeCode, awayCode, overrides)], 'fp-grid-2'));
      }
    }
    matchesEl.innerHTML = parts.join('');
  }

  function scoreValue(value) {
    return value === '' ? '' : parseInt(value, 10);
  }

  function readOverrides() {
    const out = {};
    matchesEl.querySelectorAll('.fp-match').forEach(div => {
      const key = div.dataset.key;
      if (div.dataset.kind === 'group') {
        const [h, a] = div.querySelectorAll('input');
        if (h.value !== '' || a.value !== '')
          out[key] = [scoreValue(h.value), scoreValue(a.value)];
        return;
      }
      // ko
      const inputs = div.querySelectorAll('input');
      if (inputs.length < 2) return;   // TBD case has no inputs
      const h = inputs[0].value, a = inputs[1].value;
      const homeCode = div.dataset.home, awayCode = div.dataset.away;
      const pick = div.querySelector('select[data-pick="winner"]');
      const bothFilled = h !== '' && a !== '';
      let winner = null, score = null;
      if (bothFilled) {
        const hn = parseInt(h, 10), an = parseInt(a, 10);
        score = [hn, an];
        if (hn > an) winner = homeCode;
        else if (an > hn) winner = awayCode;
        else if (pick && pick.value) winner = pick.value;
      } else if (pick && pick.value) {
        winner = pick.value;
      }
      if (winner || h !== '' || a !== '') {
        const o = {};
        if (winner) o.winner = winner;
        if (h !== '' || a !== '') o.score = [scoreValue(h), scoreValue(a)];
        out[key] = o;
      }
    });
    return out;
  }

  // -------- Run --------

  function withFocusPreserved(fn) {
    const active = document.activeElement;
    const matchKey = active && active.closest && active.closest('.fp-match')?.dataset.key;
    const side = active?.dataset?.side;
    const tag = active?.tagName;
    const eloCode = active?.classList?.contains('elo-input') ? active.dataset.code : null;
    const selStart = active?.selectionStart;
    fn();
    let target = null;
    if (matchKey && side) {
      target = matchesEl.querySelector(`.fp-match[data-key="${matchKey}"] input[data-side="${side}"]`);
    } else if (matchKey && tag === 'SELECT') {
      target = matchesEl.querySelector(`.fp-match[data-key="${matchKey}"] select[data-pick="winner"]`);
    } else if (eloCode) {
      target = resultsEl.querySelector(`input.elo-input[data-code="${eloCode}"]`);
    }
    if (target) {
      target.focus();
      if (selStart != null && target.setSelectionRange) {
        try { target.setSelectionRange(selStart, selStart); } catch (e) {}
      }
    }
  }

  function setStatus(text, progress = null) {
    statusTextEl.textContent = text;
    statusFillEl.style.width = progress == null ? '0' : `${progress * 100}%`;
    statusEl.classList.toggle('running', progress != null);
  }

  function run({rerenderMatches = true} = {}) {
    const generation = ++runGeneration;
    readElos();
    const uiOverrides = readOverrides();
    const overrides = completeOverrides(uiOverrides);
    saveOverrides(uiOverrides);
    if (rerenderMatches) withFocusPreserved(() => renderMatches());
    runBtn.disabled = true;
    const nFixed = Object.keys(overrides).length;
    setStatus(`0 / ${N_SIMS.toLocaleString()} simulations`, 0);
    setTimeout(() => {
      const t0 = performance.now();
      const stats = new Map();
      let completed = 0;
      const chunk = () => {
        if (generation !== runGeneration) return;
        const chunkStart = performance.now();
        while (completed < N_SIMS && performance.now() - chunkStart < 80) {
          const batch = Math.min(250, N_SIMS - completed);
          Football.simulateInto(config, batch, overrides, model, stats);
          completed += batch;
        }
        withFocusPreserved(() => renderResults(stats, completed));
        const elapsed = Math.round(performance.now() - t0);
        if (completed < N_SIMS) {
          setStatus(
            `${completed.toLocaleString()} / ${N_SIMS.toLocaleString()} simulations`,
            completed / N_SIMS);
          setTimeout(chunk, 0);
          return;
        }
        setStatus(
          `${N_SIMS.toLocaleString()} sims in ${elapsed} ms` +
          (nFixed ? ` · ${nFixed} fixed` : ''));
        runBtn.disabled = false;
      };
      chunk();
    }, 10);
  }

  let autoRunTimer = null;
  function scheduleAutoRun() {
    clearTimeout(autoRunTimer);
    autoRunTimer = setTimeout(() => run(), 400);
  }

  // -------- Wire up --------

  resultsEl.addEventListener('click', e => {
    if (e.target.closest('#fp-expand')) {
      expanded = !expanded;
      renderResults(latestStats, latestNSims);
    }
  });

  function refreshMatchState(div) {
    if (div.dataset.kind === 'group') {
      const [h, a] = div.querySelectorAll('input');
      div.classList.toggle('fixed', h.value !== '' && a.value !== '');
      return;
    }
    const inputs = div.querySelectorAll('input');
    if (inputs.length < 2) return;
    const h = inputs[0].value, a = inputs[1].value;
    const both = h !== '' && a !== '';
    const equal = both && h === a;
    const pk = div.querySelector('select[data-pick="winner"]');
    if (pk) {
      pk.hidden = !equal;
      // Whenever the picker isn't visible, its value can't be edited;
      // make sure it doesn't silently contribute a stale winner override.
      if (pk.hidden) pk.value = '';
    }
    let fixed = false;
    if (both && !equal) fixed = true;
    else if (equal && pk && pk.value) fixed = true;
    else if (!both && pk && pk.value) fixed = true;
    div.classList.toggle('fixed', fixed);
  }
  matchesEl.addEventListener('input', e => {
    const div = e.target.closest('.fp-match');
    if (div) refreshMatchState(div);
    scheduleAutoRun();
  });
  matchesEl.addEventListener('change', e => {
    const div = e.target.closest('.fp-match');
    if (div) refreshMatchState(div);
    scheduleAutoRun();
  });
  resultsEl.addEventListener('input', e => {
    if (e.target.classList && e.target.classList.contains('elo-input')) {
      scheduleAutoRun();
    }
  });
  document.getElementById('fp-model').addEventListener('input', e => {
    if (e.target.matches('input[type="range"]')) {
      readModelControls();
      scheduleAutoRun();
    }
  });

  runBtn.addEventListener('click', run);
  tournamentSelect.addEventListener('change', () => {
    const url = new URL(window.location.href);
    url.searchParams.set('tournament', tournamentSelect.value);
    window.location.assign(url);
  });
  loadBtn.addEventListener('click', () => {
    const ovr = actualOverrides();
    saveOverrides(ovr);
    renderMatches();
    run();
  });
  resetBtn.addEventListener('click', () => {
    saveOverrides({});
    renderMatches();
    run();
  });
  modelResetBtn.addEventListener('click', () => {
    model = { ...Football.DEFAULT_MODEL };
    saveModel(model);
    renderModelControls();
    refreshBacktestUi({ clearChangedResults: true });
    run();
  });
  backtestBtn.addEventListener('click', runBacktest);

  // Initial paint
  renderModelControls();
  refreshBacktestUi();
  renderResults(null);
  renderMatches();
  run();
  loadLiveResults();
})();
