// Monte Carlo tournament simulator. Port of simulate.py.
// Works in browser (exposes window.Football) and node (module.exports).
(function (root) {
  const BASE_XG = 1.35;
  const ELO_TO_XG = 0.0025;   // per-match calibration on 51K historical matches
  const MIN_XG = 0.25;
  const ET_FRACTION = 1 / 3;
  const PK_ELO_DAMP = 1200;
  const HOST_ADVANTAGE_DEFAULT = 50;   // ELO bonus for host country teams

  const DEFAULT_MODEL = Object.freeze({
    eloStrength: 1,
    hostAdvantage: HOST_ADVANTAGE_DEFAULT,
    laterRoundEffect: 1,
    recency: 0,
  });

  function modelSettings(settings) {
    return { ...DEFAULT_MODEL, ...(settings || {}) };
  }

  function effectiveElos(config, settings) {
    // Returns { code: effective_elo }. Adds the host bonus when the team
    // is listed in config.hosts.
    const model = modelSettings(settings);
    const hosts = new Set(config.hosts || []);
    const boost = config.host_advantage != null
      ? config.host_advantage : model.hostAdvantage;
    const out = {};
    for (const [code, info] of Object.entries(config.teams)) {
      const trend = info.elo_9m == null ? 0 : info.elo - info.elo_9m;
      out[code] = info.elo + trend * model.recency + (hosts.has(code) ? boost : 0);
    }
    return out;
  }

  const H2H = new Set(['h2h_points', 'h2h_gd', 'h2h_gf']);
  const DEFAULT_CRITERIA = ['points', 'gd', 'gf', 'h2h_points', 'h2h_gd', 'random'];

  function poisson(mean) {
    const L = Math.exp(-mean);
    let k = 0, p = 1.0;
    while (true) {
      k += 1;
      p *= Math.random();
      if (p <= L) return k - 1;
    }
  }

  function stageSlopeFactor(stageId, settings) {
    // Late-stage fatigue / pressure: dampen ELO weight so favorites have
    // less advantage as the tournament wears on. laterRoundEffect=0 removes
    // the effect; 1 is the calibrated model; 2 doubles it.
    const effect = modelSettings(settings).laterRoundEffect;
    if (stageId === 'final') return Math.max(0, 1 - 0.5 * effect);
    if (stageId === 'sf' || stageId === 'third_place') return Math.max(0, 1 - 0.3 * effect);
    return 1.0;
  }

  function expectedGoals(eloA, eloB, slopeFactor = 1.0, settings) {
    const eff = ELO_TO_XG * modelSettings(settings).eloStrength * slopeFactor;
    const xgA = Math.max(MIN_XG, BASE_XG + (eloA - eloB) * eff);
    const xgB = Math.max(MIN_XG, BASE_XG - (eloA - eloB) * eff);
    return [xgA, xgB];
  }

  function regulationGoals(eloA, eloB, slopeFactor = 1.0, settings) {
    const [xgA, xgB] = expectedGoals(eloA, eloB, slopeFactor, settings);
    return [poisson(xgA), poisson(xgB)];
  }

  function knockoutWinner(nameA, eloA, nameB, eloB, slopeFactor = 1.0, settings) {
    const [ga, gb] = regulationGoals(eloA, eloB, slopeFactor, settings);
    if (ga !== gb) return ga > gb ? [nameA, nameB] : [nameB, nameA];
    // Tied after regulation: coin flip. This stands in for extra time and
    // penalty shootouts; empirically those outcomes are close to 50/50
    // regardless of ELO, and the coin flip also absorbs "form of the day"
    // variance our model otherwise can't see.
    return Math.random() < 0.5 ? [nameA, nameB] : [nameB, nameA];
  }

  function outcomeProbabilitiesFromXg(xgA, xgB) {
    const maxGoals = 20;
    const distributions = [xgA, xgB].map(mean => {
      const out = [Math.exp(-mean)];
      for (let k = 1; k <= maxGoals; k++) out.push(out[k - 1] * mean / k);
      return out;
    });
    let win = 0, draw = 0, loss = 0;
    for (let a = 0; a <= maxGoals; a++) {
      for (let b = 0; b <= maxGoals; b++) {
        const p = distributions[0][a] * distributions[1][b];
        if (a > b) win += p;
        else if (a === b) draw += p;
        else loss += p;
      }
    }
    const total = win + draw + loss;
    return { win: win / total, draw: draw / total, loss: loss / total };
  }

  function outcomeProbabilities(eloDiff, settings, slopeFactor = 1.0) {
    const [xgA, xgB] = expectedGoals(eloDiff, 0, slopeFactor, settings);
    return outcomeProbabilitiesFromXg(xgA, xgB);
  }

  // --- Score prediction -----------------------------------------------------
  // simulate() samples to estimate how often teams reach each round. The
  // functions below answer a different question: the single most-likely
  // *scoreline* of one match — what you need to fill in a score-prediction
  // pool. Mirror of predict.py; keep the two in sync.

  const BLOWOUT_GAP_DEFAULT = 440;    // raw ELO gap that calls an outright 4-0
  const MARKET_WEIGHT_DEFAULT = 0.8;  // weight on market xG when blending

  function marketXg(ah, ou) {
    // Convert bookmaker lines to expected goals for [home, away]. The
    // Asian-handicap line is the negative of the home team's supremacy and the
    // over/under line is the expected total: xgHome - xgAway = -ah,
    // xgHome + xgAway = ou.
    const supremacy = -ah;
    return [
      Math.max(MIN_XG, (ou + supremacy) / 2),
      Math.max(MIN_XG, (ou - supremacy) / 2),
    ];
  }

  function poissonMode(mean) {
    // Mode of a Poisson(mean): floor(mean), floored at our minimum xG.
    return Math.floor(Math.max(MIN_XG, mean));
  }

  function scorelineProbabilities(xgA, xgB, top = null, maxGoals = 10) {
    // Scorelines ranked by probability (independent Poissons). With top set,
    // return only that many of the most likely ones.
    const dists = [xgA, xgB].map(mean => {
      const out = [Math.exp(-mean)];
      for (let k = 1; k <= maxGoals; k++) out.push(out[k - 1] * mean / k);
      return out;
    });
    const grid = [];
    for (let a = 0; a <= maxGoals; a++)
      for (let b = 0; b <= maxGoals; b++)
        grid.push([[a, b], dists[0][a] * dists[1][b]]);
    grid.sort((x, y) => y[1] - x[1]);
    return top ? grid.slice(0, top) : grid;
  }

  function outcomeAwareScore(xgA, xgB) {
    // Most likely scoreline consistent with the most likely result. The plain
    // modal score over-predicts draws; conditioning on the win/draw/loss
    // outcome first scores better in pool backtests.
    const { win, draw, loss } = outcomeProbabilitiesFromXg(xgA, xgB);
    const best = (win >= draw && win >= loss) ? 'w' : (draw >= loss ? 'd' : 'l');
    const ok = ([a, b]) => best === 'w' ? a > b : best === 'd' ? a === b : a < b;
    for (const [s] of scorelineProbabilities(xgA, xgB)) if (ok(s)) return s;
    return [1, 1];
  }

  function predictedScore(eloA, eloB, options = {}) {
    // Most-likely scoreline for A vs B. eloA/eloB are effective ELOs (host
    // bonus included) and drive the xG model. Options:
    //   market       {ah, ou}  blend market xG with model xG before the mode
    //   marketWeight  0..1      weight on market xG (default 0.8)
    //   blowout       number    raw ELO gap for an outright 4-0 (null disables)
    //   rawDiff       number    no-host gap gating the blowout (default eloA-eloB)
    const {
      market = null,
      marketWeight = MARKET_WEIGHT_DEFAULT,
      blowout = BLOWOUT_GAP_DEFAULT,
      rawDiff = eloA - eloB,
      outcomeAware = false,
      top = null,
      slopeFactor = 1.0,
      settings,
    } = options;
    let [xgA, xgB] = expectedGoals(eloA, eloB, slopeFactor, settings);
    let blended = false;
    if (market) {
      const [mA, mB] = marketXg(market.ah, market.ou);
      xgA = marketWeight * mA + (1 - marketWeight) * xgA;
      xgB = marketWeight * mB + (1 - marketWeight) * xgB;
      blended = true;
    }
    let [a, b] = outcomeAware ? outcomeAwareScore(xgA, xgB)
                              : [poissonMode(xgA), poissonMode(xgB)];
    let isBlowout = false;
    if (blowout != null) {
      if (rawDiff >= blowout) { a = 4; b = 0; isBlowout = true; }
      else if (rawDiff <= -blowout) { a = 0; b = 4; isBlowout = true; }
    }
    const result = {
      score: [a, b],
      xg: [xgA, xgB],
      probs: outcomeProbabilitiesFromXg(xgA, xgB),
      market: blended,
      blowout: isBlowout,
    };
    if (top) {
      result.top = scorelineProbabilities(xgA, xgB, top)
        .map(([s, p]) => [s, Math.round(p * 1e4) / 1e4]);
    }
    return result;
  }

  function teamRecord(team, matches) {
    let pts = 0, gf = 0, ga = 0;
    for (const m of matches) {
      if (m.home === team) {
        gf += m.homeGoals; ga += m.awayGoals;
        if (m.homeGoals > m.awayGoals) pts += 3;
        else if (m.homeGoals === m.awayGoals) pts += 1;
      } else if (m.away === team) {
        gf += m.awayGoals; ga += m.homeGoals;
        if (m.awayGoals > m.homeGoals) pts += 3;
        else if (m.awayGoals === m.homeGoals) pts += 1;
      }
    }
    return { points: pts, gd: gf - ga, gf };
  }

  function criterionValue(crit, team, matches) {
    if (crit === 'random') return Math.random();
    const key = crit.replace('h2h_', '');
    return teamRecord(team, matches)[key];
  }

  function rankGroup(teams, matches, criteria) {
    if (teams.length <= 1 || criteria.length === 0) return [...teams];
    const [head, ...rest] = criteria;
    let scope = matches;
    if (H2H.has(head)) {
      const ts = new Set(teams);
      scope = matches.filter(m => ts.has(m.home) && ts.has(m.away));
    }
    const buckets = new Map();
    for (const t of teams) {
      const k = criterionValue(head, t, scope);
      if (!buckets.has(k)) buckets.set(k, []);
      buckets.get(k).push(t);
    }
    const out = [];
    for (const k of [...buckets.keys()].sort((a, b) => b - a)) {
      const bucket = buckets.get(k);
      if (bucket.length === 1) out.push(bucket[0]);
      else out.push(...rankGroup(bucket, matches, rest));
    }
    return out;
  }

  function resolve(ref, slots, teams) {
    if (ref in teams) return ref;
    if (slots.has(ref)) return slots.get(ref);
    throw new Error(`unresolved slot: ${ref}`);
  }

  function teamsInStage(stage, slots, teams) {
    const out = new Set();
    if (stage.type === 'groups') {
      for (const grp of Object.values(stage.groups)) {
        for (const c of grp) out.add(resolve(c, slots, teams));
      }
    } else if (stage.type === 'bracket') {
      for (const m of Object.values(stage.matches)) {
        out.add(resolve(m.home, slots, teams));
        out.add(resolve(m.away, slots, teams));
      }
    } else if (stage.type === 'match') {
      out.add(resolve(stage.home, slots, teams));
      out.add(resolve(stage.away, slots, teams));
    }
    return out;
  }

  function thirdsAssign(stage, slots, teamStats) {
    // Collect Nth-place teams from each source group, with their stats.
    const sourceId = stage.source;
    const sourceStage = stage._sourceStage;
    const candidates = [];
    for (const groupName of Object.keys(sourceStage.groups)) {
      const team = slots.get(`${sourceId}.${groupName}.${stage.rank}`);
      if (!team) continue;
      const stats = teamStats.get(team) || { points: 0, gd: 0, gf: 0 };
      candidates.push({ team, group: groupName, ...stats });
    }
    candidates.sort((a, b) =>
      b.points - a.points || b.gd - a.gd || b.gf - a.gf || Math.random() - 0.5);
    const top = candidates.slice(0, stage.take);
    const combo = top.map(c => c.group).sort().join('');
    const assignment = stage.assignments && stage.assignments[combo];
    if (assignment) {
      for (const [slotName, sourceGroup] of Object.entries(assignment)) {
        const c = candidates.find(c => c.group === sourceGroup);
        if (c) slots.set(`${stage.id}.${slotName}`, c.team);
      }
    } else {
      // Fallback: ordered assignment by rank (best third → thirds.1, etc.)
      top.forEach((c, i) => slots.set(`${stage.id}.${i + 1}`, c.team));
    }
  }

  function applySlotOverrides(stage, slots, config) {
    // Pin a slot to a specific team and swap whatever was there into the
    // other team's old position. Used for tournaments where reality
    // can't be derived from match data alone — e.g. 1954's group 2 had
    // DE-TR tied on points with no FIFA-recognised statistical tiebreaker,
    // and the playoff itself decided it.
    if (!config.slot_overrides) return;
    for (const [slot, team] of Object.entries(config.slot_overrides)) {
      if (!slot.startsWith(stage.id + '.')) continue;
      const current = slots.get(slot);
      if (current === team || current === undefined) continue;
      const groupPrefix = slot.split('.').slice(0, -1).join('.') + '.';
      let otherSlot = null;
      for (const [k, v] of slots) {
        if (v === team && k.startsWith(groupPrefix)) { otherSlot = k; break; }
      }
      if (otherSlot) {
        slots.set(slot, team);
        slots.set(otherSlot, current);
      }
    }
  }

  function simulateOnce(config, overrides, settings) {
    const { teams } = config;
    const criteria = config.tiebreakers || DEFAULT_CRITERIA;
    const elo = effectiveElos(config, settings);   // ELO + host bonus per team
    const slots = new Map();
    const reached = new Map();
    const teamStats = new Map();   // team code → {points, gd, gf}

    for (const stage of config.stages) {
      reached.set(stage.id, teamsInStage(stage, slots, teams));

      if (stage.type === 'groups') {
        for (const [name, codes] of Object.entries(stage.groups)) {
          const actual = codes.map(c => resolve(c, slots, teams));
          const matches = [];
          // Use explicit pairings if declared (seeded-group formats like
          // WC 1954); otherwise default to full round-robin.
          let pairs;
          if (stage.pairings && stage.pairings[name]) {
            pairs = stage.pairings[name].map(([a, b]) =>
              [resolve(a, slots, teams), resolve(b, slots, teams)]);
          } else {
            pairs = [];
            for (let i = 0; i < actual.length; i++) {
              for (let j = i + 1; j < actual.length; j++) {
                pairs.push([actual[i], actual[j]]);
              }
            }
          }
          for (const [a, b] of pairs) {
            const fixed = overrides && overrides[`${stage.id}.${a}.${b}`];
            const [ga, gb] = fixed || regulationGoals(elo[a], elo[b], 1, settings);
            matches.push({ home: a, away: b, homeGoals: ga, awayGoals: gb });
          }
          // Record cross-group-comparable stats before ranking.
          for (const code of actual) {
            teamStats.set(code, { ...teamRecord(code, matches), group: name });
          }
          const ranked = rankGroup(actual, matches, criteria);
          ranked.forEach((code, i) => slots.set(`${stage.id}.${name}.${i + 1}`, code));
        }
      } else if (stage.type === 'thirds_pool') {
        // Cache resolved source stage on first encounter.
        if (!stage._sourceStage) {
          stage._sourceStage = config.stages.find(s => s.id === stage.source);
          if (!stage._sourceStage) {
            throw new Error(`thirds_pool: source stage "${stage.source}" not found`);
          }
        }
        thirdsAssign(stage, slots, teamStats);
      } else if (stage.type === 'bracket') {
        for (const [mid, mdef] of Object.entries(stage.matches)) {
          const a = resolve(mdef.home, slots, teams);
          const b = resolve(mdef.away, slots, teams);
          const ovr = overrides && overrides[`${stage.id}.${mid}`];
          let w, l;
          if (ovr && (ovr.winner === a || ovr.winner === b)) {
            w = ovr.winner;
            l = w === a ? b : a;
          } else {
            [w, l] = knockoutWinner(
              a, elo[a], b, elo[b], stageSlopeFactor(stage.id, settings), settings);
          }
          slots.set(`${stage.id}.${mid}.W`, w);
          slots.set(`${stage.id}.${mid}.L`, l);
        }
      } else if (stage.type === 'match') {
        const a = resolve(stage.home, slots, teams);
        const b = resolve(stage.away, slots, teams);
        const ovr = overrides && overrides[stage.id];
        let w, l;
        if (ovr && (ovr.winner === a || ovr.winner === b)) {
          w = ovr.winner;
          l = w === a ? b : a;
        } else {
          [w, l] = knockoutWinner(
            a, elo[a], b, elo[b], stageSlopeFactor(stage.id, settings), settings);
        }
        slots.set(`${stage.id}.W`, w);
        slots.set(`${stage.id}.L`, l);
      }
      applySlotOverrides(stage, slots, config);
    }
    return { slots, reached };
  }

  function simulateInto(config, n, overrides, settings, stats = new Map()) {
    const stageIds = config.stages.map(s => s.id);
    const championSlot = config.champion_slot || `${stageIds[stageIds.length - 1]}.W`;
    const inc = (team, key) => {
      if (!stats.has(team)) stats.set(team, new Map());
      const c = stats.get(team);
      c.set(key, (c.get(key) || 0) + 1);
    };
    for (let i = 0; i < n; i++) {
      const { slots, reached } = simulateOnce(config, overrides, settings);
      for (const [sid, ts] of reached) for (const t of ts) inc(t, sid);
      const champ = slots.get(championSlot);
      if (champ) inc(champ, 'champion');
    }
    return stats;
  }

  function simulate(config, n, overrides, settings) {
    return simulateInto(config, n, overrides, settings);
  }

  // Given the current overrides, lock slots that resolve deterministically.
  // Used by the UI to know which knockout match participants are settled.
  // Excludes the `random` tiebreaker — true ties leave teams in YAML order.
  function resolveDeterministic(config, overrides) {
    const { teams } = config;
    const criteria = (config.tiebreakers || DEFAULT_CRITERIA).filter(c => c !== 'random');
    const slots = new Map();
    const teamStats = new Map();
    const tryResolve = (ref) => {
      if (ref in teams) return ref;
      if (slots.has(ref)) return slots.get(ref);
      return null;
    };
    for (const stage of config.stages) {
      if (stage.type === 'groups') {
        for (const [name, codes] of Object.entries(stage.groups)) {
          const actual = codes.map(tryResolve);
          if (actual.some(x => x === null)) continue;
          const matches = [];
          let allFixed = true;
          for (let i = 0; i < actual.length && allFixed; i++) {
            for (let j = i + 1; j < actual.length; j++) {
              const a = actual[i], b = actual[j];
              const fixed = overrides && overrides[`${stage.id}.${a}.${b}`];
              if (!fixed) { allFixed = false; break; }
              matches.push({ home: a, away: b, homeGoals: fixed[0], awayGoals: fixed[1] });
            }
          }
          if (!allFixed) continue;
          for (const code of actual) {
            teamStats.set(code, { ...teamRecord(code, matches), group: name });
          }
          const ranked = rankGroup(actual, matches, criteria);
          ranked.forEach((c, i) => slots.set(`${stage.id}.${name}.${i + 1}`, c));
        }
      } else if (stage.type === 'thirds_pool') {
        if (!stage._sourceStage) {
          stage._sourceStage = config.stages.find(s => s.id === stage.source);
          if (!stage._sourceStage) continue;
        }
        // Only resolve if every source group's Nth slot is set.
        const sourceGroups = Object.keys(stage._sourceStage.groups);
        const ready = sourceGroups.every(g => slots.has(`${stage.source}.${g}.${stage.rank}`));
        if (!ready) continue;
        thirdsAssign(stage, slots, teamStats);
      } else if (stage.type === 'bracket') {
        for (const [mid, mdef] of Object.entries(stage.matches)) {
          const a = tryResolve(mdef.home);
          const b = tryResolve(mdef.away);
          if (a === null || b === null) continue;
          const ovr = overrides && overrides[`${stage.id}.${mid}`];
          if (!ovr || (ovr.winner !== a && ovr.winner !== b)) continue;
          slots.set(`${stage.id}.${mid}.W`, ovr.winner);
          slots.set(`${stage.id}.${mid}.L`, ovr.winner === a ? b : a);
        }
      } else if (stage.type === 'match') {
        const a = tryResolve(stage.home);
        const b = tryResolve(stage.away);
        if (a === null || b === null) continue;
        const ovr = overrides && overrides[stage.id];
        if (!ovr || (ovr.winner !== a && ovr.winner !== b)) continue;
        slots.set(`${stage.id}.W`, ovr.winner);
        slots.set(`${stage.id}.L`, ovr.winner === a ? b : a);
      }
    }
    return slots;
  }

  const api = {
    DEFAULT_MODEL, effectiveElos, simulate, simulateInto, simulateOnce, regulationGoals, knockoutWinner,
    outcomeProbabilities, rankGroup, resolveDeterministic, stageSlopeFactor,
    marketXg, poissonMode, predictedScore, scorelineProbabilities, outcomeAwareScore,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.Football = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
