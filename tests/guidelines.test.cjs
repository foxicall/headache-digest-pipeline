const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const guides = require('../assets/guidelines.js');
const data = JSON.parse(fs.readFileSync(path.join(__dirname, '../data/headache_guidelines.json')));
const copy = () => structuredClone(data);

test('published guideline data validates and covers Taiwan, Japan and nine European countries', () => {
  assert.equal(guides.validate(data), data);
  const ids = new Set(data.jurisdictions.map((j) => j.id));
  for (const id of ['tw', 'jp', 'uk-nice', 'uk-sign', 'de', 'fr', 'it', 'es', 'nl', 'dk', 'ch', 'fi', 'eu']) assert.ok(ids.has(id));
  assert.equal(data.comparisons.filter((r) => r.syndrome === 'migraine').length, ids.size);
  assert.ok(data.sources.every((s) => s.reviewed_on === data.reviewed_on));
});
test('syndrome and selected-country filters intersect without mixing recommendations', () => {
  const rows = guides.filterRows(data, 'trigeminal-neuralgia', ['jp', 'ch', 'tw']);
  assert.deepEqual(rows.map((r) => r.id), ['jp-trigeminal-neuralgia', 'ch-trigeminal-neuralgia']);
  assert.deepEqual(guides.filterRows(data, 'migraine', []), []);
});
test('missing Taiwan cluster coverage is explicit and not replaced by EAN', () => {
  assert.deepEqual(guides.filterRows(data, 'cluster', ['tw']), []);
  assert.deepEqual(guides.missingRegions(data, 'cluster', ['tw', 'jp']).map((j) => j.id), ['tw']);
});
test('acute and preventive Taiwan versions remain independent', () => {
  const row = guides.filterRows(data, 'migraine', ['tw'])[0];
  assert.deepEqual(row.domains.acute.source_ids, ['tw-acute-2026']);
  assert.deepEqual(row.domains.prevention.source_ids, ['tw-prevent-2022']);
  assert.equal(row.domains.access.status, 'not_verified');
});
test('Japanese first-line statement is distinguished from historical access conditions', () => {
  const row = guides.filterRows(data, 'migraine', ['jp'])[0];
  assert.match(row.domains.prevention.text, /第一線/);
  assert.match(row.domains.access.text, /不等於無條件給付/);
  assert.match(data.sources.find((s) => s.id === 'jp-cgrp-2025').kind, /立場聲明/);
});
test('unknown access differs from topics outside the document scope', () => {
  const row = guides.filterRows(data, 'migraine', ['nl'])[0];
  assert.equal(row.domains.acute.status, 'not_covered');
  assert.ok(row.domains.acute.source_ids.length);
});
for (const [name, mutate] of [
  ['duplicate source', (d) => d.sources.push(d.sources[0])],
  ['missing source id', (d) => { delete d.sources[0].id; }],
  ['unresolved clinical citation', (d) => { d.comparisons[0].domains.acute.source_ids = ['no-source']; }],
  ['uncited clinical recommendation', (d) => { d.comparisons[0].domains.acute.source_ids = []; }],
  ['missing source locator', (d) => { d.comparisons[0].domains.acute.locator = ''; }],
  ['duplicate country-syndrome pair', (d) => d.comparisons.push({...d.comparisons[0], id:'duplicate'})],
  ['unrecognized syndrome', (d) => { d.comparisons[0].syndrome = 'anything'; }],
  ['missing comparison field', (d) => { delete d.comparisons[0].domains.access; }],
  ['JavaScript source URL', (d) => { d.sources[0].url = 'javascript:alert(1)'; }],
  ['URL with credentials', (d) => { d.sources[0].url = 'https://secret@example.org'; }],
  ['invalid review date', (d) => { d.reviewed_on = '2026-02-30'; }],
  ['version newer than review', (d) => { d.sources[0].version_date = '2027'; }],
  ['unrecognized evidence provenance', (d) => { d.sources[0].verification = 'guessed'; }],
]) test(`rejects ${name}`, () => { const d = copy(); mutate(d); assert.throws(() => guides.validate(d)); });
test('version freshness does not pretend daily paper updates re-review guidelines', () => {
  assert.equal(guides.stale(data, new Date('2026-10-18T00:00:00Z')), false);
  assert.equal(guides.stale(data, new Date('2027-02-01T00:00:00Z')), true);
});

test('service worker caches verified guideline JSON, preserves it on invalid response and serves it offline', async () => {
  const listeners = {}, cached = new Map();
  let response = () => Promise.resolve(new Response(JSON.stringify(data), {status:200}));
  const scope = 'https://example.test/headache-digest-pipeline/';
  const sandbox = {
    self: {registration: {scope}, addEventListener: (event, handler) => {listeners[event] = handler;}, DigestGuidelines:guides},
    importScripts: () => {}, URL, Response, Headers, fetch: (...args) => response(...args),
    caches: {open: async () => ({put: async (req, res) => cached.set(req.url, res), match: async (req) => cached.get(req.url)?.clone()})},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../sw.js'), 'utf8'), sandbox);
  const url = scope + 'data/headache_guidelines.json';
  async function request() { let result; listeners.fetch({request:{method:'GET',url}, respondWith: (p) => {result=p;}}); return await result; }
  assert.equal((await (await request()).json()).schema_version, 1);
  response = async () => new Response(JSON.stringify({schema_version:1, sources:[], comparisons:[]}));
  const fallback = await request();
  assert.equal(fallback.headers.get('X-Digest-Cache'), 'offline');
  assert.deepEqual(await fallback.json(), data);
  response = async () => { throw new Error('network offline'); };
  assert.deepEqual(await (await request()).json(), data);
});
