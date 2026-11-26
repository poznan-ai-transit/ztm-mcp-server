// Usage: node lib/report.js <results.json> [--by-test]   (from evals/)
//   --by-test  group the summary by test id (A1, M3, ...) instead of by category letter.
//              Default for multi-step results (all ids start with "M").
const fs = require('fs');

const args = process.argv.slice(2);
const file = args.find((a) => !a.startsWith('--')) || 'results/multi-step.json';
const data = JSON.parse(fs.readFileSync(file, 'utf8'));

const rows = Array.isArray(data.results) ? data.results : (data.results && data.results.results) || [];

const text = (v) => (typeof v === 'string' ? v : JSON.stringify(v)) || '';
const short = (v, n) => text(v).replace(/\s+/g, ' ').slice(0, n);
const providerName = (provider) => {
  if (typeof provider === 'string') return provider.replace(/^openrouter:/, '');
  if (provider && typeof provider === 'object') {
    return (provider.label || provider.id || provider.provider || 'unknown').replace(/^openrouter:/, '');
  }
  return 'unknown';
};
const description = (row) => (row.testCase && row.testCase.description) || row.description || '?';
const testId = (row) => (description(row).match(/^([A-Z]\d+)/) || [, 'other'])[1];
const byTest = args.includes('--by-test') || rows.every((r) => /^M\d/.test(description(r)));
const groupName = (row) => (byTest ? testId(row) : testId(row).replace(/\d+$/, ''));
const toolSequence = (row) =>
  ((row.response && row.response.metadata && row.response.metadata.toolCalls) || []).map((c) => c.name).join(' > ');

const summary = new Map();
for (const row of rows) {
  const model = providerName(row.provider);
  const group = groupName(row);
  const key = `${model}\0${group}`;
  const entry = summary.get(key) || { model, group, passed: 0, total: 0 };
  entry.total++;
  if (row.success) entry.passed++;
  summary.set(key, entry);
}

console.log('SUMMARY');
console.log('============');
console.log(`${'Model'.padEnd(42)} ${(byTest ? 'Test' : 'Category').padEnd(10)} Result`);
const entries = [...summary.values()].sort((a, b) =>
  a.model.localeCompare(b.model) || a.group.localeCompare(b.group, undefined, { numeric: true }));
for (const entry of entries) {
  const rate = entry.total ? ((entry.passed / entry.total) * 100).toFixed(1) : '0.0';
  console.log(`${entry.model.padEnd(42)} ${entry.group.padEnd(10)} ${entry.passed}/${entry.total} (${rate}%)`);
}

console.log('');
const models = [...new Set(rows.map((r) => providerName(r.provider)))];
for (const model of models) {
  const own = rows.filter((r) => providerName(r.provider) === model);
  const ok = own.filter((r) => r.success).length;
  console.log(`${model.padEnd(42)} total      ${ok}/${own.length} (${((ok / own.length) * 100).toFixed(1)}%)`);
}
const passed = rows.filter((row) => row.success).length;
const overallRate = rows.length ? ((passed / rows.length) * 100).toFixed(1) : '0.0';
console.log(`Overall: ${passed}/${rows.length} (${overallRate}%)`);
console.log('');

let failed = 0;
for (const r of rows) {
  if (r.success) continue;
  failed++;
  const comps = (r.gradingResult && r.gradingResult.componentResults) || [];
  const reasons = comps.filter((c) => !c.pass).map((c) => '  - ' + short(c.reason, 400));
  const error = r.error || (r.response && r.response.error);
  console.log(`[${providerName(r.provider)}] ${description(r)}`);
  if (error) console.log('  ERROR: ' + short(error, 400));
  console.log(reasons.length ? reasons.join('\n') : '  - ' + short(r.gradingResult && r.gradingResult.reason, 400));
  console.log('  tools: ' + (toolSequence(r) || '(none)'));
  console.log('  output: ' + short(r.response && r.response.output, 400));
  console.log('');
}
console.log(`Failed: ${failed} out of ${rows.length}`);
