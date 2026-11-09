// Uzycie: node failures.js results.json
// Wypisuje podsumowanie per model/kategoria oraz oblane testy.
const fs = require('fs');

const file = process.argv[2] || 'results.json';
const data = JSON.parse(fs.readFileSync(file, 'utf8'));

// Rozne wersje promptfoo trzymaja wyniki w .results.results albo .results
const rows = Array.isArray(data.results) ? data.results : (data.results && data.results.results) || [];

const text = (v) => (typeof v === 'string' ? v : JSON.stringify(v)) || '';
const short = (v, n) => text(v).replace(/\s+/g, ' ').slice(0, n);
const providerName = (provider) => {
  if (typeof provider === 'string') return provider.replace(/^openrouter:/, '');
  if (provider && typeof provider === 'object') {
    return provider.id || provider.label || provider.provider || 'unknown';
  }
  return 'unknown';
};
const categoryName = (row) => {
  const description = (row.testCase && row.testCase.description) || row.description || '?';
  const match = description.match(/^([A-Z])/);
  return match ? match[1] : 'other';
};

const summary = new Map();
for (const row of rows) {
  const model = providerName(row.provider);
  const category = categoryName(row);
  const key = `${model}\0${category}`;
  const entry = summary.get(key) || { model, category, passed: 0, total: 0 };
  entry.total++;
  if (row.success) entry.passed++;
  summary.set(key, entry);
}

console.log('PODSUMOWANIE');
console.log('============');
console.log('Model                                      Kategoria  Wynik');
for (const entry of summary.values()) {
  const rate = entry.total ? ((entry.passed / entry.total) * 100).toFixed(1) : '0.0';
  console.log(
    `${entry.model.padEnd(42)} ${entry.category.padEnd(10)} ${entry.passed}/${entry.total} (${rate}%)`,
  );
}
const passed = rows.filter((row) => row.success).length;
const overallRate = rows.length ? ((passed / rows.length) * 100).toFixed(1) : '0.0';
console.log(`Razem: ${passed}/${rows.length} (${overallRate}%)`);
console.log('');

let failed = 0;
for (const r of rows) {
  if (r.success) continue;
  failed++;
  const name = (r.testCase && r.testCase.description) || r.description || '?';
  const comps = (r.gradingResult && r.gradingResult.componentResults) || [];
  const reasons = comps.filter((c) => !c.pass).map((c) => '  - ' + short(c.reason, 200));
  console.log(name);
  console.log(reasons.length ? reasons.join('\n') : '  - ' + short(r.gradingResult && r.gradingResult.reason, 200));
  console.log('  output: ' + short(r.response && r.response.output, 250));
  console.log('');
}
console.log(`Oblane: ${failed} z ${rows.length}`);