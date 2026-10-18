/* Versioned, independently sourced guideline comparisons; no AI or API key needed. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DigestGuidelines = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const DOMAINS = Object.freeze({
    acute: "急性／發作期治療", prevention: "預防／長期治療",
    escalation: "進階／專科治療", access: "給付與使用條件",
  });
  const READ_LEVEL = { full_text: "全文／原始建議", abstract_only: "原始摘要", official_summary: "官方摘要" };
  const DEFAULT_REGIONS = ["tw", "jp", "uk-nice", "de", "eu"];
  const GRADE_STATUS = { reported: "原文分級", not_reported: "所引段落未報告", not_verified: "尚未核實" };
  const string = (value) => typeof value === "string" && value.trim().length > 0;
  function httpsUrl(value) {
    try { const url = new URL(value); return url.protocol === "https:" && !!url.hostname && !url.username && !url.password; }
    catch { return false; }
  }
  function validDate(value, partial = false) {
    if (typeof value !== "string" || !(partial ? /^\d{4}(?:-\d{2}(?:-\d{2})?)?$/ : /^\d{4}-\d{2}-\d{2}$/).test(value)) return false;
    const full = value.length === 4 ? `${value}-01-01` : value.length === 7 ? `${value}-01` : value;
    const date = new Date(`${full}T00:00:00Z`);
    return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === full;
  }
  function validate(data) {
    const fail = () => { throw new Error("指引資料格式不完整，請稍後重新載入。"); };
    if (data?.schema_version !== 2 || !validDate(data.reviewed_on) ||
        !string(data.coverage_note) || !string(data.update_note) || !string(data.grading_note)) fail();
    function index(list) {
      if (!Array.isArray(list) || !list.length) fail();
      const map = new Map();
      for (const item of list) {
        if (!item || !string(item.id) || !/^[a-z0-9-]+$/.test(item.id) || map.has(item.id)) fail();
        map.set(item.id, item);
      }
      return map;
    }
    const countries = index(data.jurisdictions), syndromes = index(data.syndromes), sources = index(data.sources);
    const systems = index(data.grading_systems);
    for (const system of systems.values()) {
      if (![system.label, system.description].every(string)) fail();
      for (const key of ['recommendation_values', 'evidence_values']) {
        if (!Array.isArray(system[key]) || !system[key].every(string) || new Set(system[key]).size !== system[key].length) fail();
      }
    }
    for (const item of countries.values()) if (!string(item.label) || !["asia", "europe", "pan-european"].includes(item.group)) fail();
    for (const item of syndromes.values()) if (!string(item.label)) fail();
    for (const source of sources.values()) {
      if (![source.title, source.publisher, source.kind, source.scope, source.version_note].every(string) ||
          !httpsUrl(source.url) || !validDate(source.version_date, true) || !validDate(source.reviewed_on) ||
          source.version_date > source.reviewed_on || source.reviewed_on > data.reviewed_on ||
          !Object.hasOwn(READ_LEVEL, source.verification) || !Array.isArray(source.grading_system_ids) ||
          !source.grading_system_ids.length || source.grading_system_ids.some((id) => !systems.has(id))) fail();
    }
    const rows = index(data.comparisons), pairs = new Set();
    for (const row of rows.values()) {
      const pair = `${row.jurisdiction}:${row.syndrome}`;
      if (!countries.has(row.jurisdiction) || !syndromes.has(row.syndrome) || pairs.has(pair) || !string(row.population) ||
          !Array.isArray(row.source_ids) || !row.source_ids.length || new Set(row.source_ids).size !== row.source_ids.length ||
          row.source_ids.some((id) => !sources.has(id))) fail();
      pairs.add(pair);
      if (row.grading_review !== undefined && !string(row.grading_review)) fail();
      if (!row.domains || Object.keys(row.domains).length !== Object.keys(DOMAINS).length) fail();
      for (const key of Object.keys(DOMAINS)) {
        const item = row.domains[key];
        if (!item || !["summarized", "not_covered", "not_verified"].includes(item.status) || !string(item.text) ||
            !Array.isArray(item.source_ids) || item.source_ids.some((id) => !row.source_ids.includes(id))) fail();
        if (item.status !== "not_verified" && (!item.source_ids.length || !string(item.locator))) fail();
      }
      // A grade belongs to a specific source, clinical question and regimen.
      const recommendations = index(row.recommendations);
      for (const item of recommendations.values()) {
        const source = sources.get(item.source_id), system = systems.get(item.grading_system);
        if (![item.drug, item.population, item.locator].every(string) || !source || !system ||
            !row.source_ids.includes(item.source_id) || !source.grading_system_ids.includes(item.grading_system) ||
            !Array.isArray(item.domains) || !item.domains.length || new Set(item.domains).size !== item.domains.length ||
            item.domains.some((key) => !['acute', 'prevention', 'escalation'].includes(key)) ||
            !validDate(item.reviewed_on) || item.reviewed_on > source.reviewed_on ||
            !['regimen', 'wording', 'note'].every((key) => typeof item[key] === 'string')) fail();
        for (const [key, allowed] of [['recommendation', system.recommendation_values], ['evidence', system.evidence_values]]) {
          const grade = item[key];
          if (!grade || !Object.hasOwn(GRADE_STATUS, grade.status)) fail();
          if (grade.status === 'reported' ? !allowed.includes(grade.value) : grade.value !== null) fail();
        }
        if (item.recommendation.status !== 'reported' && !string(item.wording)) fail();
        if ((item.recommendation.status !== 'reported' || item.evidence.status !== 'reported') && !string(item.note)) fail();
        if (item.evidence_context !== undefined && !string(item.evidence_context)) fail();
        if (item.evidence_profiles !== undefined) {
          // Outcome/study-specific ratings must not imply an overall drug grade.
          if (!Array.isArray(item.evidence_profiles) || !item.evidence_profiles.length ||
              item.evidence.status !== 'not_reported' || item.evidence.value !== null) fail();
          const profiles = new Set();
          for (const profile of item.evidence_profiles) {
            const evidenceSource = sources.get(profile?.source_id);
            if (!profile || ![profile.outcome, profile.locator].every(string) || !evidenceSource ||
                !row.source_ids.includes(profile.source_id) ||
                !evidenceSource.grading_system_ids.includes(item.grading_system) ||
                item.reviewed_on > evidenceSource.reviewed_on || typeof profile.reason !== 'string' ||
                (profile.value === null ? !string(profile.reason) : !system.evidence_values.includes(profile.value))) fail();
            const key = JSON.stringify([profile.outcome, profile.value, profile.source_id, profile.locator]);
            if (profiles.has(key)) fail();
            profiles.add(key);
          }
        }
      }
    }
    return data;
  }
  function filterRows(data, syndrome, jurisdictions) {
    const selected = new Set(jurisdictions);
    return data.comparisons.filter((row) => row.syndrome === syndrome && selected.has(row.jurisdiction));
  }
  function missingRegions(data, syndrome, jurisdictions) {
    const represented = new Set(filterRows(data, syndrome, jurisdictions).map((row) => row.jurisdiction));
    return data.jurisdictions.filter((region) => jurisdictions.includes(region.id) && !represented.has(region.id));
  }
  function filterRecommendations(row, domain = 'all') {
    return row.recommendations.filter((item) => domain === 'all' || item.domains.includes(domain));
  }
  function stale(data, now = new Date()) {
    return now.getTime() - Date.parse(`${data.reviewed_on}T00:00:00Z`) > 90 * 86400000;
  }
  let mounted = false;
  async function mount() {
    if (mounted) return;
    mounted = true;
    const $ = (id) => document.getElementById(id);
    if (!$('guidelines')) return;
    const node = (tag, className, text) => {
      const result = document.createElement(tag);
      if (className) result.className = className;
      if (text !== undefined) result.textContent = text;
      return result;
    };
    const link = (source, text) => {
      const result = node("a", "guideline-source-link", text);
      result.href = source.url; result.target = "_blank"; result.rel = "noopener noreferrer";
      result.title = `${source.title} · ${source.version_date}`;
      return result;
    };
    let data = null, cached = false, busy = false, retryAfterControllerChange = false;
    let selected = [...DEFAULT_REGIONS];
    let syndrome = "migraine", domain = "all", format = "grades";
    function controls(disabled) {
      for (const id of ["guideline-syndrome", "guideline-domain", "guideline-format", "guideline-countries"]) $(id).disabled = disabled;
      document.querySelectorAll("[data-guideline-preset]").forEach((button) => { button.disabled = disabled; });
    }
    function render() {
      if (!data) return;
      const rows = filterRows(data, syndrome, selected);
      const sources = new Map(data.sources.map((s) => [s.id, s]));
      const countries = new Map(data.jurisdictions.map((s) => [s.id, s]));
      const systems = new Map(data.grading_systems.map((s) => [s.id, s]));
      const domains = domain === "all" ? Object.keys(DOMAINS) : [domain];
      $('guidelines-results').dataset.domain = domain;
      $('guidelines-results').dataset.format = format;
      $('guidelines-selection').textContent = `${selected.length} / ${data.jurisdictions.length}`;
      const drugCount = rows.reduce((count, row) => count + filterRecommendations(row, domain).length, 0);
      $('guidelines-status').textContent = `${rows.length} 個地區／組織可比較 · ${data.syndromes.find((s) => s.id === syndrome).label}` +
        (format === 'grades' && domain !== 'access' ? ` · ${drugCount} 項藥物建議（同藥不同情境分列）` : '') +
        (cached ? " · 離線快取，內容截至標示查核日" : "");
      const gaps = missingRegions(data, syndrome, selected);
      $('guidelines-gaps').hidden = !gaps.length;
      $('guidelines-gaps').textContent = `本症候群尚未整理或未核實：${gaps.map((g) => g.label).join('、')}。這不代表當地沒有指引；不會套用其他國家或偏頭痛的建議。`;
      const cards = rows.map((row) => {
        const card = node('article', 'guideline-card'); card.dataset.guidelineId = row.id;
        const heading = node('div', 'guideline-identity');
        const h = node('h3', '', countries.get(row.jurisdiction).label); h.id = `guide-${row.id}`;
        card.setAttribute('aria-labelledby', h.id);
        heading.append(h, node('p', 'guideline-population', row.population));
        const versions = node('div', 'guideline-versions');
        const shownVersions = new Set();
        for (const id of row.source_ids) {
          const s = sources.get(id);
          const version = `${s.version_date} · ${s.kind}`;
          if (!shownVersions.has(version)) versions.append(node('span', 'guideline-version', version));
          shownVersions.add(version);
        }
        heading.append(versions);
        if (row.grading_review) heading.append(node('p', 'guideline-grading-review', row.grading_review));
        const drugSection = node('section', 'guideline-drugs');
        const drugHeading = node('h4', '', '藥物推薦與證據等級');
        drugHeading.id = `drugs-${row.id}`;
        drugSection.setAttribute('aria-labelledby', drugHeading.id);
        drugSection.append(drugHeading);
        const recommendations = filterRecommendations(row, domain);
        if (!recommendations.length) drugSection.append(node('p', 'guideline-drug-empty', '此比較重點尚無已整理的藥物分級；可切換重點或查看治療概覽。'));
        else {
          const table = node('table', 'guideline-drug-table'); table.setAttribute('role', 'table');
          const caption = node('caption', 'sr-only', `${countries.get(row.jurisdiction).label} · 藥物 COR／LOE`);
          const thead = node('thead'), tr = node('tr');
          thead.setAttribute('role', 'rowgroup'); tr.setAttribute('role', 'row');
          for (const text of ['藥物／適用情境', 'Class of Recommendation', 'Level of Evidence', '分級制度／來源']) {
            const th = node('th', '', text); th.scope = 'col'; th.setAttribute('role', 'columnheader'); tr.append(th);
          }
          thead.append(tr);
          const tbody = node('tbody'); tbody.setAttribute('role', 'rowgroup');
          for (const item of recommendations) {
            const entry = node('tr'); entry.setAttribute('role', 'row'); entry.dataset.recommendationId = item.id;
            const drug = node('th', 'drug-name'); drug.scope = 'row'; drug.setAttribute('role', 'rowheader');
            drug.append(node('strong', '', item.drug), node('span', 'drug-population', item.population));
            drug.append(node('span', 'drug-phase', item.domains.map((key) => DOMAINS[key]).join(' · ')));
            if (item.regimen) drug.append(node('small', 'drug-regimen', `原文評級療程：${item.regimen}`));
            entry.append(drug);
            for (const [key, label] of [['recommendation', 'Class of Recommendation'], ['evidence', 'Level of Evidence']]) {
              const grade = item[key], cell = node('td', `drug-grade drug-${key}`); cell.setAttribute('role', 'cell');
              const mobileLabel = node('span', 'drug-mobile-label', label); mobileLabel.setAttribute('aria-hidden', 'true');
              cell.append(mobileLabel);
              if (key === 'evidence' && item.evidence_profiles) {
                cell.dataset.gradeStatus = 'profiles';
                cell.append(node('span', 'grade-profile-heading', '依結果／證據段落分列'));
                const profiles = node('ul', 'evidence-profiles');
                for (const profile of item.evidence_profiles) {
                  const li = node('li'), evidenceSource = sources.get(profile.source_id);
                  li.append(node('span', 'evidence-outcome', profile.outcome),
                    node('span', profile.value === null ? 'grade-unavailable' : 'grade-value', profile.value ?? profile.reason),
                    link(evidenceSource, `${evidenceSource.version_date} · 來源 ${row.source_ids.indexOf(profile.source_id) + 1} ↗`),
                    node('small', 'drug-locator', profile.locator));
                  profiles.append(li);
                }
                cell.append(profiles);
              } else {
                cell.append(node('span', grade.status === 'reported' ? 'grade-value' : 'grade-unavailable', grade.status === 'reported' ? grade.value : GRADE_STATUS[grade.status]));
                cell.dataset.gradeStatus = grade.status;
              }
              if (key === 'evidence' && item.evidence_context) cell.append(node('small', 'grade-wording', item.evidence_context));
              if (key === 'recommendation' && item.wording) cell.append(node('small', 'grade-wording', `原文建議：${item.wording}`));
              entry.append(cell);
            }
            const provenance = node('td', 'drug-provenance'); provenance.setAttribute('role', 'cell');
            provenance.append(node('span', 'drug-system', systems.get(item.grading_system).label));
            if (item.note) provenance.append(node('p', '', item.note));
            provenance.append(link(sources.get(item.source_id), `來源 ${row.source_ids.indexOf(item.source_id) + 1} ↗`), node('small', 'drug-locator', item.locator));
            entry.append(provenance); tbody.append(entry);
          }
          table.append(caption, thead, tbody); drugSection.append(table);
        }
        const fields = node('dl', 'guideline-fields');
        for (const key of domains) {
          const item = row.domains[key], cell = node('div', `guideline-cell ${key === 'access' ? 'guideline-access' : ''}`);
          cell.append(node('dt', '', DOMAINS[key]));
          const dd = node('dd');
          if (item.status !== 'summarized') dd.append(node('span', 'guideline-status-label', item.status === 'not_covered' ? '此版本未涵蓋' : '尚未核實'));
          dd.append(node('p', '', item.text));
          const refs = node('div', 'guideline-refs');
          for (const id of item.source_ids) refs.append(link(sources.get(id), `來源 ${row.source_ids.indexOf(id) + 1} ↗`));
          if (item.locator) refs.append(node('small', '', item.locator));
          dd.append(refs); cell.append(dd); fields.append(cell);
        }
        const details = node('details', 'guideline-sources');
        details.append(node('summary', '', '查看原文版本與查核範圍'));
        const list = node('ol');
        for (const id of row.source_ids) {
          const s = sources.get(id), li = node('li');
          li.append(link(s, s.title + ' ↗'),
            node('p', '', `${s.publisher} · ${s.version_date} · ${s.kind}`),
            node('p', '', `${READ_LEVEL[s.verification]} · 查核 ${s.reviewed_on} · 範圍：${s.scope}`),
            node('p', '', s.version_note));
          list.append(li);
        }
        details.append(list); card.append(heading);
        if (format === 'grades' && domain !== 'access') {
          card.append(drugSection);
          const overview = node('details', 'guideline-overview');
          overview.append(node('summary', '', '治療情境與使用條件'), fields); card.append(overview);
        } else card.append(fields);
        card.append(details); return card;
      });
      if (!cards.length) cards.push(node('p', 'guidelines-empty', selected.length ? '目前所選地區尚無此症候群的已核實比較。請更換症候群或地區。' : '請勾選至少一個地區開始比較。'));
      $('guidelines-results').replaceChildren(...cards);
    }
    function selectRegions(ids) {
      selected = ids;
      document.querySelectorAll('[data-guideline-country]').forEach((input) => { input.checked = selected.includes(input.value); });
      render();
    }
    async function load() {
      if (busy) return;
      busy = true; controls(true);
      $('guidelines-retry').hidden = true;
      $('guidelines-status').textContent = '正在讀取指引比較…';
      $('guidelines-results').setAttribute('aria-busy', 'true');
      const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
      try {
        const response = await fetch('./data/headache_guidelines.json', { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error('目前無法讀取指引資料；文獻書架仍可獨立使用。');
        data = validate(await response.json());
        cached = response.headers.get('X-Digest-Cache') === 'offline';
        $('guidelines-reviewed').textContent = `版本查核 ${data.reviewed_on}`;
        $('guidelines-coverage').textContent = data.coverage_note;
        $('guidelines-update-note').textContent = data.update_note;
        $('guidelines-grading-note').textContent = data.grading_note;
        $('guidelines-grading-systems').replaceChildren(...data.grading_systems.map((system) => {
          const item = node('div', 'grading-system-definition');
          item.append(node('h4', '', system.label), node('p', '', system.description)); return item;
        }));
        $('guidelines-freshness').hidden = !stale(data);
        $('guidelines-freshness').textContent = '距上次版本查核已超過 90 天，可能已有新版；請優先確認原文網站。';
        $('guideline-syndrome').replaceChildren(...data.syndromes.map((s) => {
          const option = node('option', '', s.label); option.value = s.id; return option;
        }));
        if (!data.syndromes.some((s) => s.id === syndrome)) syndrome = data.syndromes[0].id;
        $('guideline-syndrome').value = syndrome;
        $('guideline-domain').value = domain;
        $('guideline-format').value = format;
        selected = selected.filter((id) => data.jurisdictions.some((j) => j.id === id));
        const legend = node('legend', 'sr-only', '可複選國家與指引組織');
        $('guideline-countries').replaceChildren(legend, ...data.jurisdictions.map((region) => {
          const label = node('label', 'guideline-country'), input = node('input');
          input.type = 'checkbox'; input.value = region.id; input.dataset.guidelineCountry = region.id;
          input.checked = selected.includes(region.id);
          input.addEventListener('change', () => { selected = [...document.querySelectorAll('[data-guideline-country]:checked')].map((i) => i.value); render(); });
          label.append(input, node('span', '', region.label)); return label;
        }));
        controls(false); render();
      } catch (error) {
        data = null;
        $('guidelines-results').replaceChildren();
        $('guidelines-gaps').hidden = true;
        $('guidelines-reviewed').textContent = '指引資料暫時無法載入';
        $('guidelines-status').textContent = error.name === 'AbortError' ? '讀取指引逾時，請重試。' : error.message;
        $('guidelines-retry').hidden = false;
      } finally {
        clearTimeout(timer); busy = false;
        $('guidelines-results').setAttribute('aria-busy', 'false');
        // A previous worker may return the old schema while an update activates.
        if (retryAfterControllerChange) {
          retryAfterControllerChange = false;
          if (!data) void load();
        }
      }
    }
    if ('serviceWorker' in navigator) navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (data) return;
      if (busy) retryAfterControllerChange = true;
      else void load();
    });
    $('guideline-syndrome').addEventListener('change', (e) => { syndrome = e.target.value; render(); });
    $('guideline-domain').addEventListener('change', (e) => { domain = e.target.value; render(); });
    $('guideline-format').addEventListener('change', (e) => { format = e.target.value; render(); });
    $('guidelines-retry').addEventListener('click', load);
    document.querySelectorAll('[data-guideline-preset]').forEach((button) => button.addEventListener('click', () => {
      if (!data) return;
      const preset = button.dataset.guidelinePreset;
      selectRegions(data.jurisdictions.filter((j) => preset === 'all' || (preset === 'europe' ? ['europe', 'pan-european'].includes(j.group) : j.group === preset)).map((j) => j.id));
    }));
    return load();
  }
  return { DOMAINS, validate, filterRows, filterRecommendations, missingRegions, stale, mount };
});
