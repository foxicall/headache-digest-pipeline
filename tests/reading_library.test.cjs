const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createLibrary} = require('../assets/reading-library.js');
const paper = JSON.parse(fs.readFileSync(path.join(__dirname, '../data/headache_papers.json')))[0];
const id = paper.article_metadata.pmid_or_id;
const validate = p => {
  assert.equal(typeof p.article_metadata.pmid_or_id, 'string');
  assert.equal(Object.keys(p.structured_summary).length, 7);
  assert.equal(typeof p.structured_summary.participants_feature, 'string');
};
function storage() {
  const values = new Map();
  return {
    get length() { return values.size; },
    key: i => [...values.keys()][i] ?? null,
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); },
    removeItem: key => { values.delete(key); },
  };
}
const library = (data, scope = '/headache-digest-pipeline/') => createLibrary(() => data, scope, validate);

test('read and saved marks survive a new instance; save includes the full original snapshot', () => {
  const data = storage(), first = library(data);
  assert.equal(first.toggleRead(id), true);
  assert.equal(first.toggleSaved(paper), true);
  const reloaded = library(data).load();
  assert(reloaded.read.has(id));
  assert.deepEqual(reloaded.saved.get(id).paper, paper);
  assert(Number.isFinite(Date.parse(reloaded.saved.get(id).savedAt)));
  assert.equal(reloaded.damaged, false);
});

test('unreading keeps the saved snapshot; unsaving keeps the read mark', () => {
  const data = storage(), marks = library(data);
  marks.toggleRead(id); marks.toggleSaved(paper);
  assert.equal(marks.toggleRead(id), false);
  assert(marks.load().saved.has(id));
  marks.toggleRead(id);
  assert.equal(marks.toggleSaved(paper), false);
  assert(marks.load().read.has(id));
  assert.equal(marks.load().saved.size, 0);
});

test('saving again is a reversible toggle without duplicate entries', () => {
  const data = storage(), marks = library(data);
  marks.toggleSaved(paper); marks.toggleSaved(paper); marks.toggleSaved(paper);
  assert.equal(marks.load().saved.size, 1);
});

test('independent instances update fresh values and cannot overwrite other paper flags', () => {
  const data = storage(), a = library(data), b = library(data);
  a.toggleSaved(paper); b.toggleRead(id); a.toggleRead('another-id');
  assert.deepEqual([...b.load().read].sort(), [id, 'another-id'].sort());
  assert(b.load().saved.has(id));
  b.toggleRead(id);
  assert(!a.load().read.has(id));
  assert(a.load().read.has('another-id'));
});

test('libraries for different GitHub Pages project paths remain isolated', () => {
  const data = storage(), a = library(data, '/one/'), b = library(data, '/two/');
  a.toggleRead(id); a.toggleSaved(paper);
  assert.equal(b.load().read.size, 0);
  assert.equal(b.load().saved.size, 0);
  b.toggleRead(id);
  a.toggleRead(id);
  assert(b.load().read.has(id));
});

test('denied storage and quota errors never return a successful mark or delete existing saves', () => {
  const denied = createLibrary(() => { throw new Error('SecurityError'); }, '/', validate);
  assert.throws(() => denied.load());
  assert.throws(() => denied.toggleRead(id));
  const data = storage(), marks = library(data);
  marks.toggleSaved(paper);
  data.setItem = () => { throw new Error('QuotaExceededError'); };
  assert.throws(() => marks.toggleRead(id));
  const other = structuredClone(paper); other.article_metadata.pmid_or_id = 'another-id';
  assert.throws(() => marks.toggleSaved(other));
  assert.equal(marks.load().read.size, 0);
  assert.equal(marks.load().saved.size, 1);
  assert.deepEqual(marks.load().saved.get(id).paper, paper);
});

test('malformed JSON, ID mismatch, old schema and invalid read flags are isolated', () => {
  const data = storage(), marks = library(data);
  marks.toggleSaved(paper); marks.toggleRead(id);
  const prefix = marks.prefix;
  data.setItem(prefix + 'saved:bad-json', '{');
  data.setItem(prefix + 'saved:wrong-id', JSON.stringify({version: 1, savedAt: new Date().toISOString(), paper}));
  data.setItem(prefix + 'read:bad-read', 'false');
  data.setItem(prefix + 'saved:%XX', 'broken');
  const old = structuredClone(paper); old.article_metadata.pmid_or_id = 'old';
  old.structured_summary.mechanism_of_action_moa = old.structured_summary.participants_feature;
  delete old.structured_summary.participants_feature;
  data.setItem(prefix + 'saved:old', JSON.stringify({version: 1, savedAt: new Date().toISOString(), paper: old}));
  const loaded = marks.load();
  assert.equal(loaded.damaged, true);
  assert.deepEqual([...loaded.read], [id]);
  assert.deepEqual([...loaded.saved.keys()], [id]);
  assert.equal(data.getItem(prefix + 'saved:bad-json'), '{');
});

test('a corrupted snapshot is not silently overwritten on a save toggle', () => {
  const data = storage(), marks = library(data);
  data.setItem(marks.prefix + 'saved:' + encodeURIComponent(id), '{');
  assert.throws(() => marks.toggleSaved(paper));
  assert.equal(data.getItem(marks.prefix + 'saved:' + encodeURIComponent(id)), '{');
});

test('an invalid paper cannot be saved', () => {
  const data = storage(), marks = library(data);
  const invalid = structuredClone(paper); delete invalid.structured_summary.participants_feature;
  assert.throws(() => marks.toggleSaved(invalid));
  assert.equal(data.length, 0);
});

test('storage clearing is reflected on next load', () => {
  const data = storage(), marks = library(data);
  marks.toggleRead(id); marks.toggleSaved(paper);
  while (data.length) data.removeItem(data.key(0));
  assert.equal(marks.load().read.size, 0);
  assert.equal(marks.load().saved.size, 0);
});
