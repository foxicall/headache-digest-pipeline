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
    if (data?.schema_version !== 1 || !validDate(data.reviewed_on) ||
        !string(data.coverage_note) || !string(data.update_note)) fail();
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
    for (const item of countries.values()) if (!string(item.label) || !["asia", "europe", "pan-european"].includes(item.group)) fail();
    for (const item of syndromes.values()) if (!string(item.label)) fail();
    for (const source of sources.values()) {
      if (![source.title, source.publisher, source.kind, source.scope, source.version_note].every(string) ||
          !httpsUrl(source.url) || !validDate(source.version_date, true) || !validDate(source.reviewed_on) ||
          source.version_date > source.reviewed_on || source.reviewed_on > data.reviewed_on ||
          !Object.hasOwn(READ_LEVEL, source.verification)) fail();
    }
    const rows = index(data.comparisons), pairs = new Set();
    for (const row of rows.values()) {
      const pair = `${row.jurisdiction}:${row.syndrome}`;
      if (!countries.has(row.jurisdiction) || !syndromes.has(row.syndrome) || pairs.has(pair) || !string(row.population) ||
          !Array.isArray(row.source_ids) || !row.source_ids.length || new Set(row.source_ids).size !== row.source_ids.length ||
          row.source_ids.some((id) => !sources.has(id))) fail();
      pairs.add(pair);
      if (!row.domains || Object.keys(row.domains).length !== Object.keys(DOMAINS).length) fail();
      for (const key of Object.keys(DOMAINS)) {
        const item = row.domains[key];
        if (!item || !["summarized", "not_covered", "not_verified"].includes(item.status) || !string(item.text) ||
            !Array.isArray(item.source_ids) || item.source_ids.some((id) => !row.source_ids.includes(id))) fail();
        if (item.status !== "not_verified" && (!item.source_ids.length || !string(item.locator))) fail();
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
    let data = null, cached = false, busy = false;
    let selected = [...DEFAULT_REGIONS];
    let syndrome = "migraine", domain = "all";
    function controls(disabled) {
      for (const id of ["guideline-syndrome", "guideline-domain", "guideline-countries"]) $(id).disabled = disabled;
      document.querySelectorAll("[data-guideline-preset]").forEach((button) => { button.disabled = disabled; });
    }
    function render() {
      if (!data) return;
      const rows = filterRows(data, syndrome, selected);
      const sources = new Map(data.sources.map((s) => [s.id, s]));
      const countries = new Map(data.jurisdictions.map((s) => [s.id, s]));
      const domains = domain === "all" ? Object.keys(DOMAINS) : [domain];
      $('guidelines-results').dataset.domain = domain;
      $('guidelines-selection').textContent = `${selected.length} / ${data.jurisdictions.length}`;
      $('guidelines-status').textContent = `${rows.length} 個地區／組織可比較 · ${data.syndromes.find((s) => s.id === syndrome).label}` +
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
        for (const id of row.source_ids) {
          const s = sources.get(id);
          versions.append(node('span', 'guideline-version', `${s.version_date} · ${s.kind}`));
        }
        heading.append(versions);
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
        details.append(list); card.append(heading, fields, details); return card;
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
        $('guidelines-freshness').hidden = !stale(data);
        $('guidelines-freshness').textContent = '距上次版本查核已超過 90 天，可能已有新版；請優先確認原文網站。';
        $('guideline-syndrome').replaceChildren(...data.syndromes.map((s) => {
          const option = node('option', '', s.label); option.value = s.id; return option;
        }));
        if (!data.syndromes.some((s) => s.id === syndrome)) syndrome = data.syndromes[0].id;
        $('guideline-syndrome').value = syndrome;
        $('guideline-domain').value = domain;
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
      }
    }
    $('guideline-syndrome').addEventListener('change', (e) => { syndrome = e.target.value; render(); });
    $('guideline-domain').addEventListener('change', (e) => { domain = e.target.value; render(); });
    $('guidelines-retry').addEventListener('click', load);
    document.querySelectorAll('[data-guideline-preset]').forEach((button) => button.addEventListener('click', () => {
      if (!data) return;
      const preset = button.dataset.guidelinePreset;
      selectRegions(data.jurisdictions.filter((j) => preset === 'all' || (preset === 'europe' ? ['europe', 'pan-european'].includes(j.group) : j.group === preset)).map((j) => j.id));
    }));
    return load();
  }
  return { DOMAINS, validate, filterRows, missingRegions, stale, mount };
});
