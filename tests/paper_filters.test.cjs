const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const filters = require('../assets/paper-filters.js');
const read = name => JSON.parse(fs.readFileSync(path.join(__dirname, '../data/', name)));
const catalog = read('headache_syndromes.json');
const matchers = filters.buildSyndromeMatchers(catalog);
const paper = (title, methods = '', design = 'Not reported', inclusion = []) => ({
  article_metadata: {title, study_design: design},
  structured_summary: {methods, inclusion_criteria: inclusion, exclusion_criteria: [], pitfalls_and_limitations: []},
});

test('trial, RCT and phase facets use indexed types; randomization is not inferred', () => {
  assert.deepEqual([...filters.classifyStudy(paper('Migraine', '', 'Clinical Trial, Phase III; Randomized Controlled Trial; Multicenter Study'))].sort(), ['clinical-trial', 'multicenter', 'phase-iii', 'rct']);
  const tags = filters.classifyStudy(paper('Migraine intervention', '', 'Clinical Trial'));
  assert(tags.has('clinical-trial') && !tags.has('nonrandomized'));
  assert(!filters.classifyStudy(paper('Results of a clinical trial')).has('clinical-trial'));
});
for (const [category, title, methods, design] of [
  ['case-report', 'Migraine: a case report', '', 'Not reported'],
  ['case-report', 'Migraine', '', 'Case Reports'],
  ['case-series', 'SUNCT: a case series', '', 'Not reported'],
  ['cohort', 'Migraine risks', '回溯性世代研究，使用保險資料。', 'Not reported'],
  ['cohort', 'A prospective cohort study of migraine', '', 'Not reported'],
  ['case-control', 'Trigeminal neuralgia: a case–control study', '', 'Not reported'],
  ['cross-sectional', 'A cross-sectional study of headache', '', 'Not reported'],
  ['systematic-review', 'Migraine', '', 'Systematic Review'],
  ['meta-analysis', 'Migraine: a network meta-analysis', '', 'Not reported'],
  ['scoping-review', 'A scoping review of headache', '', 'Not reported'],
  ['guideline', 'Clinical practice guideline for headache', '', 'Not reported'],
  ['consensus', 'Migraine', '使用 Delphi method 制定建議。', 'Not reported'],
  ['protocol', 'Migraine: study protocol', '', 'Not reported'],
  ['diagnostic', 'A diagnostic accuracy study of SUNCT', '', 'Not reported'],
  ['validation', 'Migraine', '', 'Validation Study'],
  ['qualitative', 'Migraine', '本研究採用質性研究。', 'Not reported'],
  ['mixed-methods', 'A mixed-methods study', '', 'Not reported'],
  ['economic', 'A cost-effectiveness analysis of migraine care', '', 'Not reported'],
  ['secondary-analysis', 'Migraine: a post-hoc analysis', '', 'Randomized Controlled Trial'],
  ['nonrandomized', 'A nonrandomized controlled trial of migraine treatment', '', 'Clinical Trial'],
  ['crossover', 'A randomized cross-over trial of migraine treatment', '', 'Clinical Trial'],
  ['pilot', 'A feasibility study of headache care', '', 'Not reported'],
  ['preclinical', 'A preclinical study of migraine', '', 'Not reported'],
  ['editorial', 'Headache care', '', 'Editorial'],
  ['letter', 'Headache care', '', 'Letter'],
]) test(`recognizes ${category}: ${title} ${methods}`, () => {
  assert(filters.classifyStudy(paper(title, methods, design)).has(category));
});
test('reviews and protocols do not inherit cited or planned primary-study designs', () => {
  for (const design of ['Systematic Review', 'Clinical Trial Protocol', 'Editorial']) {
    const tags = filters.classifyStudy(paper('Migraine evidence', 'A cohort study, case report and randomized trial were considered.', design));
    for (const id of ['cohort', 'case-report', 'clinical-trial', 'rct']) assert(!tags.has(id));
  }
});
test('case report forms and single participants do not imply case reports', () => {
  for (const methods of ['Electronic case report forms were used.', '使用電子病例報告表。', 'One participant completed the trial.']) {
    assert(!filters.classifyStudy(paper('Migraine', methods)).has('case-report'));
  }
});
test('unknown and negated designs or suggestions in limitations stay unclassified', () => {
  for (const methods of ['Not reported', 'This was not a cohort study.', 'A cohort study was not performed.', '本研究並非病例對照研究。']) {
    assert.deepEqual([...filters.classifyStudy(paper('Migraine', methods))], ['unclassified']);
  }
  const p = paper('Migraine'); p.structured_summary.pitfalls_and_limitations = ['Need a cohort study.'];
  assert.deepEqual([...filters.classifyStudy(p)], ['unclassified']);
});
test('indexed trial and secondary-analysis facets can overlap', () => {
  const tags = filters.classifyStudy(paper('Post-hoc analysis of migraine', '', 'Randomized Controlled Trial'));
  assert(tags.has('clinical-trial') && tags.has('rct') && tags.has('secondary-analysis'));
});
test('syndrome descendants roll up only to their ancestors', () => {
  const tags = filters.classifySyndromes(paper('Chronic cluster headache'), matchers);
  assert(tags.has('3.1.2') && tags.has('3.1') && tags.has('3'));
  assert(!tags.has('3.1.1') && !tags.has('1') && !tags.has('13'));
});
test('bilingual names, acronyms, hyphens and plurals work', () => {
  for (const [title, code] of [['SUNCT', '3.3.1'], ['SUNA', '3.3.2'], ['NDPH', '4.10'], ['偏頭痛', '1'], ['三叉神經痛', '13.1'], ['Tension-type headaches', '2'], ['Vestibular migraine', 'A1.6.6'], ['posttraumatic headache', '5']]) {
    assert(filters.classifySyndromes(paper(title), matchers).has(code), title);
  }
});
test('generic headache does not imply an unspecified diagnosis or appendix subtype', () => {
  assert.deepEqual([...filters.classifySyndromes(paper('Headache outcomes'), matchers)], ['unclassified']);
  const tags = filters.classifySyndromes(paper('Migraine without aura'), matchers);
  assert(tags.has('1.1') && !tags.has('A1.1') && !tags.has('1.2'));
  assert(filters.classifySyndromes(paper('Chronic migraine: alternative criteria'), matchers).has('A1.3'));
});
test('context guards, exclusions and limitations prevent unrelated syndrome facets', () => {
  for (const title of ['Optic neuritis outcomes', 'CADASIL outcomes', 'Central post-stroke pain in the hand']) {
    assert.deepEqual([...filters.classifySyndromes(paper(title), matchers)], ['unclassified']);
  }
  assert(filters.classifySyndromes(paper('Painful optic neuritis'), matchers).has('13.5'));
  const p = paper('Migraine prevention', 'Patients without cluster headache were recruited.');
  p.structured_summary.exclusion_criteria = ['Trigeminal neuralgia'];
  p.structured_summary.pitfalls_and_limitations = ['Consider occipital neuralgia.'];
  const tags = filters.classifySyndromes(p, matchers);
  assert(tags.has('1') && !tags.has('3.1') && !tags.has('13.1') && !tags.has('13.4'));
});
test('all 368 catalog names are selectable and roll up to existing codes', () => {
  const codes = new Set(catalog.entries.map(e => e.code));
  for (const entry of catalog.entries) {
    const title = entry.name + (entry.code.startsWith('A') ? ': Appendix' : '');
    const tags = filters.classifySyndromes(paper(title, 'Headache and migraine with facial pain.'), matchers);
    assert(tags.has(entry.code), entry.code + ' ' + entry.name);
    for (const code of tags) assert(codes.has(code), code);
  }
});
test('real cohort is recognized despite a missing PubMed study-design tag', () => {
  const cohort = read('headache_papers.json').find(p => p.article_metadata.pmid_or_id === '42862583');
  assert(filters.classifyStudy(cohort).has('cohort'));
  assert(filters.classifySyndromes(cohort, matchers).has('1'));
});
