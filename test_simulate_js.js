// Node test driver: read tournament JSON from stdin, run simulator, print
// probability table in the same format as simulate.py.
const { simulate } = require('./static/simulate.js');

let raw = '';
process.stdin.on('data', d => (raw += d));
process.stdin.on('end', () => {
  const config = JSON.parse(raw);
  const n = parseInt(process.env.N || '10000', 10);
  const stats = simulate(config, n);

  const stageIds = config.stages.map(s => s.id);
  const cols = stageIds.slice(1).filter(s => s !== 'third_place').concat('champion');
  const width = Math.max(...cols.map(c => c.length)) + 2;

  console.log(`\n${config.name}  —  ${n} simulations\n`);
  const header = '  ' + 'Team'.padEnd(28) + cols.map(c => c.padStart(width)).join('');
  console.log(header);
  console.log('  ' + '-'.repeat(header.length - 2));

  const entries = Object.entries(config.teams);
  entries.sort((a, b) => (
    ((stats.get(b[0]) || new Map()).get('champion') || 0) -
    ((stats.get(a[0]) || new Map()).get('champion') || 0)
  ));
  for (const [code, info] of entries) {
    const row = stats.get(code) || new Map();
    const cells = cols.map(c => {
      const pct = ((row.get(c) || 0) / n * 100).toFixed(1) + '%';
      return pct.padStart(width);
    }).join('');
    console.log('  ' + info.name.padEnd(28) + cells);
  }
});
