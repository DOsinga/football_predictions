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

const tournaments = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const tournament of tournaments) {
  const actual = Football.simulateOnce(tournament, actualOverrides(tournament));
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
