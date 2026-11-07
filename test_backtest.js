const fs = require('fs');
const Football = require('./static/simulate.js');

function actualOverrides(tournament) {
  const out = {};
  for (const stage of tournament.stages) {
    if (stage.type === 'groups' && stage.schedule) {
      for (const entry of stage.schedule) {
        const [, , home, away, homeGoals, awayGoals] = entry;
        if (homeGoals != null && awayGoals != null) {
          out[`${stage.id}.${home}.${away}`] = [homeGoals, awayGoals];
        }
      }
    } else if (stage.type === 'bracket') {
      for (const [matchId, match] of Object.entries(stage.matches)) {
        if (match.actual?.winner) {
          out[`${stage.id}.${matchId}`] = {
            winner: match.actual.winner,
            score: match.actual.score,
          };
        }
      }
    } else if (stage.type === 'match' && stage.actual?.winner) {
      out[stage.id] = { winner: stage.actual.winner, score: stage.actual.score };
    }
  }
  return out;
}

function resolve(ref, slots, teams) {
  if (ref in teams) return ref;
  if (slots.has(ref)) return slots.get(ref);
  throw new Error(`unresolved slot: ${ref}`);
}

const tournaments = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const tournament of tournaments) {
  const overrides = actualOverrides(tournament);
  const deterministicSlots = Football.resolveDeterministic(tournament, overrides);
  for (const stage of tournament.stages) {
    if (stage.type === 'bracket') {
      for (const [matchId, match] of Object.entries(stage.matches)) {
        const actual = match.actual || {};
        if (!actual.winner || !actual.score || actual.score[0] !== actual.score[1]) continue;
        const home = resolve(match.home, deterministicSlots, tournament.teams);
        const away = resolve(match.away, deterministicSlots, tournament.teams);
        if (actual.winner !== home && actual.winner !== away) {
          throw new Error(
            `${tournament.name} ${stage.id}.${matchId}: ${actual.winner} not in ${home}-${away}`);
        }
      }
    } else if (stage.type === 'match') {
      const actual = stage.actual || {};
      if (!actual.winner || !actual.score || actual.score[0] !== actual.score[1]) continue;
      const home = resolve(stage.home, deterministicSlots, tournament.teams);
      const away = resolve(stage.away, deterministicSlots, tournament.teams);
      if (actual.winner !== home && actual.winner !== away) {
        throw new Error(`${tournament.name} ${stage.id}: ${actual.winner} not in ${home}-${away}`);
      }
    }
  }

  const actual = Football.simulateOnce(tournament, overrides);
  const stageIds = tournament.stages.map(stage => stage.id);
  const championSlot = tournament.champion_slot || `${stageIds.at(-1)}.W`;
  const champion = actual.slots.get(championSlot);
  if (!champion) {
    throw new Error(`${tournament.name}: no champion at ${championSlot}`);
  }
  const finalStage = tournament.stages.at(-1);
  const expectedChampion = finalStage.actual?.winner ||
    (tournament.name === 'World Cup 1950' ? 'UY' : null);
  if (expectedChampion && champion !== expectedChampion) {
    throw new Error(`${tournament.name}: replayed ${champion}, expected ${expectedChampion}`);
  }
}
console.log(`actual replay passed for ${tournaments.length} tournaments`);
