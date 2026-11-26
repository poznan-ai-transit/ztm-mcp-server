const calls = (ctx) => ctx.providerResponse?.metadata?.toolCalls ?? [];
const str = (v) => (typeof v === 'string' ? v : JSON.stringify(v ?? ''));
const fail = (reason) => ({ pass: false, score: 0, reason });
const ok = { pass: true, score: 1, reason: 'OK' };


// Every get_departures must use IDs that appear (as exact field values) in earlier tool results.
function idsFromSearch(output, ctx) {
  const cs = calls(ctx);
  const deps = cs.filter((c) => c.name === 'get_departures');
  if (!deps.length) return fail('No get_departures call');

  // Exact match on a field when the result is a list of objects; string fallback otherwise.
  const hasId = (result, key, value) =>
    Array.isArray(result)
      ? result.some((x) => String(x?.[key]) === value)
      : str(result).includes(`"${key}":"${value}"`);

  for (const d of deps) {
    const before = cs.slice(0, cs.indexOf(d));
    const a = d.args || {};
    for (const k of ['stop_id', 'route_id', 'date', 'time']) {
      if (!a[k]) return fail(`get_departures missing required arg: ${k}`);
    }
    if (typeof a.stop_id !== 'string') return fail('stop_id must be a JSON string (leading zeros)');
    const stop = a.stop_id;
    const route = String(a.route_id);
    if (!before.some((c) => c.name === 'search_stops' && hasId(c.result, 'stop_id', stop)))
      return fail(`stop_id ${stop} not found in any earlier search_stops result`);
    if (!before.some((c) => ['search_routes', 'get_routes_for_stop'].includes(c.name)
        && hasId(c.result, 'route_id', route)))
      return fail(`route_id ${route} not found in any earlier route lookup result`);
  }
  return ok;
}

// HH:MM occurrences, also inside "12:01:00" and ISO "2026-11-18T00:42:00" (no \b: "T0" has no boundary).
const TIME_RE = /(?<!\d)(\d{1,2}):(\d{2})(?!\d)/g;
// Normalise "9:05" -> "09:05" and GTFS overnight "24:42" -> "00:42".
const normTime = (h, m) => `${String(Number(h) % 24).padStart(2, '0')}:${m}`;
const timesIn = (s) => [...String(s).matchAll(TIME_RE)].map((m) => ({ raw: m[0], norm: normTime(m[1], m[2]) }));

// Anti-hallucination: every time mentioned in the answer must come from tool results
// or from the user's own question (e.g. the model repeating "after 12:00").
function noInventedTimes(output, ctx) {
  const data = calls(ctx).map((c) => str(c.result)).join(' ');
  const known = new Set(timesIn(data + ' ' + str(ctx.vars?.question)).map((t) => t.norm));
  for (const t of timesIn(output)) {
    if (!known.has(t.norm)) return fail(`Time ${t.raw} appears in neither tool results nor the question`);
  }
  return ok;
}

// The model did not call get_departures.
function noDeparturesCall(output, ctx) {
  return calls(ctx).some((c) => c.name === 'get_departures') ? fail('Unexpected get_departures call') : ok;
}

module.exports = { idsFromSearch, noInventedTimes, noDeparturesCall };