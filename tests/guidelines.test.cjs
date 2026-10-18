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
    caches: {open: async () => ({put: async (url, res) => cached.set(url, res), match: async (url) => cached.get(url)?.clone()})},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../sw.js'), 'utf8'), sandbox);
  const url = scope + 'data/headache_guidelines.json';
  async function request() { let result; listeners.fetch({request:{method:'GET',url}, respondWith: (p) => {result=p;}}); return await result; }
  assert.equal((await (await request()).json()).schema_version, 2);
  response = async () => new Response(JSON.stringify({schema_version:2, sources:[], comparisons:[]}));
  const fallback = await request();
  assert.equal(fallback.headers.get('X-Digest-Cache'), 'offline');
  assert.deepEqual(await fallback.json(), data);
  response = async () => { throw new Error('network offline'); };
  assert.deepEqual(await (await request()).json(), data);
});

test('offline section bookmarks use the cached document without a fragment', async () => {
  const listeners = {}, scope = 'https://example.test/headache-digest-pipeline/';
  const cached = new Map([[scope, new Response('<main>Offline reader</main>')]]);
  const sandbox = {
    self: {registration: {scope}, addEventListener: (event, handler) => {listeners[event] = handler;}, DigestGuidelines:guides},
    importScripts: () => {}, URL, Response, Headers,
    fetch: async () => { throw new Error('offline'); },
    caches: {open: async () => ({match: async (url) => cached.get(url)?.clone()})},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../sw.js'), 'utf8'), sandbox);
  for (const fragment of ['#guidelines', '#library']) {
    let response;
    listeners.fetch({request:{method:'GET',url:scope+fragment}, respondWith: (result) => {response=result;}});
    assert.ok(response, 'the worker must handle section navigation');
    assert.equal(await (await response).text(), '<main>Offline reader</main>');
  }
});

const findDrug = (country, drug, population = '') => data.comparisons.find((r) => r.id === country).recommendations.find((r) => r.drug === drug && r.population.includes(population));
test('every comparison has source-bound drug entries with explicit COR and LOE status', () => {
  for (const row of data.comparisons) {
    assert.ok(row.recommendations.length);
    for (const drug of row.recommendations) {
      assert.ok(row.source_ids.includes(drug.source_id));
      assert.ok(drug.locator);
      for (const grade of [drug.recommendation, drug.evidence]) assert.equal(grade.value === null, grade.status !== 'reported');
    }
  }
});
test('Taiwan native Group I does not mean first-line or cardiology Class I', () => {
  const drug = findDrug('tw-migraine', 'Amitriptyline');
  assert.equal(drug.recommendation.value, 'Group I');
  assert.equal(drug.evidence.value, 'Grade B');
  assert.match(drug.note, /不等同/);
  assert.equal(drug.grading_system, 'ths-2022');
});
test('Taiwan flunarizine evidence and recommendation change by population', () => {
  const em = findDrug('tw-migraine', 'Flunarizine', '陣發性');
  const cm = findDrug('tw-migraine', 'Flunarizine', '慢性');
  assert.deepEqual([em.recommendation.value, em.evidence.value], ['Group I','Grade A']);
  assert.deepEqual([cm.recommendation.value, cm.evidence.value], ['Group II','Grade B']);
});
test('Japanese triptan comparison is weak/B, not a global triptan efficacy grade', () => {
  const drug = findDrug('jp-migraine', 'Triptans（相較 NSAIDs）');
  assert.deepEqual([drug.recommendation.value, drug.evidence.value], ['弱い推奨','B']);
  assert.match(drug.population, /影響日常生活/);
});
test('EHF grades stay bound to the comparator and regimen', () => {
  const rows = data.comparisons.find((r) => r.id === 'eu-migraine').recommendations;
  const cm = rows.filter((r) => r.drug === 'Erenumab' && r.population.startsWith('慢性'));
  assert.deepEqual(cm.map((r) => [r.regimen, r.evidence.value]), [['70 mg 每月','High'],['140 mg 每月','Moderate']]);
  const headToHead = rows.find((r) => r.drug === 'Erenumab（相較 topiramate）');
  assert.deepEqual([headToHead.recommendation.value,headToHead.evidence.value],['Strong','Low']);
});
test('a later Japanese position statement is not assigned another guideline GRADE', () => {
  const drug = findDrug('jp-migraine','CGRP monoclonal antibodies');
  assert.equal(drug.source_id,'jp-cgrp-2025');
  assert.equal(drug.recommendation.status,'not_reported');
  assert.equal(drug.evidence.status,'not_reported');
});
test('a verified recommendation does not manufacture unknown evidence certainty', () => {
  const drug = findDrug('eu-cluster','Oxygen');
  assert.equal(drug.recommendation.value,'Strong');
  assert.equal(drug.evidence.status,'not_verified');
  assert.equal(drug.evidence.value,null);
});
test('NICE wording and Spanish NE-I are not recoded as Class I', () => {
  const nice = findDrug('uk-nice-migraine','Propranolol／topiramate／amitriptyline');
  assert.equal(nice.wording,'Consider');
  assert.equal(nice.recommendation.value,null);
  const es = findDrug('es-migraine','Triptans');
  assert.deepEqual([es.recommendation.value, es.evidence.value],['GR-A','NE-I']);
});
test('drug table domain filter respects tags and excludes administrative conditions', () => {
  const row = data.comparisons.find((r) => r.id === 'tw-migraine');
  assert.equal(guides.filterRecommendations(row, 'acute').length,3);
  assert.ok(guides.filterRecommendations(row, 'escalation').some((r) => r.drug === 'OnabotulinumtoxinA'));
  assert.deepEqual(guides.filterRecommendations(row, 'access'),[]);
});
for (const [name, mutate] of [
  ['old schema', (d) => {d.schema_version=1;}],
  ['fabricated harmonized COR', (d) => {d.comparisons[0].recommendations[0].recommendation.value='Class I';}],
  ['unsupported native LOE', (d) => {d.comparisons[0].recommendations[0].evidence.value='High';}],
  ['grade attached to unknown status', (d) => {d.comparisons[0].recommendations[0].evidence.status='not_verified';}],
  ['grade without a value', (d) => {d.comparisons[0].recommendations[0].evidence.value=null;}],
  ['drug with foreign source', (d) => {d.comparisons[0].recommendations[0].source_id='jp-2021';}],
  ['source using another grading scheme', (d) => {d.comparisons[0].recommendations[0].grading_system='grade';}],
  ['missing indication', (d) => {d.comparisons[0].recommendations[0].population='';}],
  ['missing drug locator', (d) => {d.comparisons[0].recommendations[0].locator='';}],
  ['administrative rule as drug grade', (d) => {d.comparisons[0].recommendations[0].domains=['access'];}],
  ['missing drug entries', (d) => {d.comparisons[0].recommendations=[];}],
]) test(`rejects ${name}`, () => {const d=copy();mutate(d);assert.throws(()=>guides.validate(d));});
