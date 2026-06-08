const assert = require('assert');
const Football = require('./static/simulate.js');

const baseline = Football.outcomeProbabilities(200);
const stronger = Football.outcomeProbabilities(200, { eloStrength: 1.5 });
const neutral = Football.outcomeProbabilities(200, { eloStrength: 0 });

assert(stronger.win > baseline.win, 'stronger ELO effect should favor the better team');
assert(stronger.loss < baseline.loss, 'stronger ELO effect should reduce upsets');
assert(Math.abs(neutral.win - neutral.loss) < 1e-12, 'zero ELO effect should make teams equal');
assert(Math.abs(baseline.win + baseline.draw + baseline.loss - 1) < 1e-12);

assert.strictEqual(Football.stageSlopeFactor('final'), 0.5);
assert.strictEqual(Football.stageSlopeFactor('sf'), 0.7);
assert.strictEqual(Football.stageSlopeFactor('qf'), 1);
assert.strictEqual(Football.stageSlopeFactor('final', { laterRoundEffect: 0 }), 1);
assert.strictEqual(Football.stageSlopeFactor('sf', { laterRoundEffect: 2 }), 0.4);

const tinyConfig = {
  teams: {
    A: { elo: 1800, elo_9m: 1700 },
    B: { elo: 1800, elo_9m: 1800 },
  },
  stages: [{ id: 'final', type: 'match', home: 'A', away: 'B' }],
};
assert.deepStrictEqual(Football.effectiveElos(tinyConfig, { recency: 0 }), { A: 1800, B: 1800 });
assert.deepStrictEqual(Football.effectiveElos(tinyConfig, { recency: .5 }), { A: 1850, B: 1800 });
assert.deepStrictEqual(Football.effectiveElos(tinyConfig, { recency: -.5 }), { A: 1750, B: 1800 });

const accumulated = Football.simulateInto(tinyConfig, 10);
Football.simulateInto(tinyConfig, 15, null, null, accumulated);
const champions = [...accumulated.values()]
  .reduce((sum, teamStats) => sum + (teamStats.get('champion') || 0), 0);
assert.strictEqual(champions, 25, 'incremental batches should accumulate into one stats map');

console.log('model settings tests passed');
