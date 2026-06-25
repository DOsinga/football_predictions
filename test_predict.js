const assert = require('assert');
const Football = require('./static/simulate.js');

// Asian-handicap + over/under -> expected goals [home, away].
assert.deepStrictEqual(Football.marketXg(0, 2.5), [1.25, 1.25], 'level line splits the total');
assert.deepStrictEqual(Football.marketXg(-0.75, 2.25), [1.5, 0.75], 'home favourite gets the supremacy');

// poissonMode is floor(xG), floored at the model minimum.
assert.strictEqual(Football.poissonMode(1.35), 1);
assert.strictEqual(Football.poissonMode(2.57), 2);
assert.strictEqual(Football.poissonMode(0.1), 0);

// Layer 1 — model modal score. Equal teams draw 1-1; a moderate edge wins 1-0.
const even = Football.predictedScore(1800, 1800, { rawDiff: 0 });
assert.deepStrictEqual(even.score, [1, 1], 'equal teams -> 1-1');
assert.strictEqual(even.blowout, false);
assert.strictEqual(even.market, false);
assert(Math.abs(even.probs.win + even.probs.draw + even.probs.loss - 1) < 1e-12);

assert.deepStrictEqual(
  Football.predictedScore(1740, 1518, { rawDiff: 222 }).score, [1, 0],
  'moderate favourite -> 1-0');

// Layer 3 — blowout tier: a raw ELO gap >= 440 forces an outright 4-0.
const blow = Football.predictedScore(2000, 1500, { rawDiff: 500 });
assert.deepStrictEqual(blow.score, [4, 0], 'raw gap >= 440 -> 4-0');
assert.strictEqual(blow.blowout, true);
assert.deepStrictEqual(
  Football.predictedScore(1500, 2000, { rawDiff: -500 }).score, [0, 4],
  'underdog blowout -> 0-4');
assert.deepStrictEqual(
  Football.predictedScore(2000, 1500, { rawDiff: 440 }).score, [4, 0],
  'threshold is inclusive');
assert.strictEqual(
  Football.predictedScore(2000, 1500, { rawDiff: 439 }).blowout, false,
  'just below threshold is not a blowout');
assert.strictEqual(
  Football.predictedScore(2000, 1500, { rawDiff: 500, blowout: null }).blowout, false,
  'blowout: null disables the tier');

// Layer 2 — market blend. Pure market (weight 1) takes the score straight from
// the lines; an 80/20 blend pulls an overshooting host pick back down.
const pureMarket = Football.predictedScore(2005, 1518, {
  rawDiff: 357, market: { ah: -0.75, ou: 2.25 }, marketWeight: 1,
});
assert.deepStrictEqual(pureMarket.score, [1, 0], 'pure market -> 1-0');
assert.strictEqual(pureMarket.market, true);

// Mexico (host +130) vs South Africa: model alone says 2-0, the market blend
// corrects the flat host bonus to the 1-0 a human picked.
assert.deepStrictEqual(
  Football.predictedScore(2005, 1518, { rawDiff: 357 }).score, [2, 0],
  'model overshoots the weak host');
assert.deepStrictEqual(
  Football.predictedScore(2005, 1518, {
    rawDiff: 357, market: { ah: -0.75, ou: 2.25 }, marketWeight: 0.8,
  }).score, [1, 0],
  'market blend corrects host overshoot');

// scorelineProbabilities — ranked, length-bounded, equal teams peak at 1-1.
const dist = Football.scorelineProbabilities(1.35, 1.35, 3);
assert.strictEqual(dist.length, 3, 'top N bounds the list');
assert.deepStrictEqual(dist[0][0], [1, 1], 'equal xG -> 1-1 is the single likeliest score');
assert(dist[0][1] >= dist[1][1] && dist[1][1] >= dist[2][1], 'sorted descending');

// outcomeAwareScore — a slight favourite: the modal score still draws 1-1, but
// a home win is the likeliest result, so outcome-aware backs the home win.
assert.deepStrictEqual(
  Football.predictedScore(1840, 1800, { rawDiff: 40 }).score, [1, 1],
  'modal over-predicts the draw');
const aware = Football.predictedScore(1840, 1800, { rawDiff: 40, outcomeAware: true }).score;
assert(aware[0] > aware[1], 'outcome-aware -> a home win');
const favAware = Football.outcomeAwareScore(2.5, 0.5);
assert(favAware[0] > favAware[1], 'clear favourite -> a home win');

// predictedScore top: carries the N most likely scorelines.
const withTop = Football.predictedScore(1800, 1800, { rawDiff: 0, top: 3 });
assert.strictEqual(withTop.top.length, 3);
assert.deepStrictEqual(withTop.top[0][0], [1, 1]);

console.log('predict tests passed');
