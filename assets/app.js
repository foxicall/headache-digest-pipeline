"use strict";

const FIELDS = [
  ["methods", "Methods · 研究方法", "text"],
  ["participants_feature", "Participants feature · 參與者特徵", "text"],
  ["number_of_inclusion", "Number of Inclusion · 納入人數", "text"],
  ["inclusion_criteria", "Inclusion Criteria · 納入條件", "list"],
  ["exclusion_criteria", "Exclusion Criteria · 排除條件", "list"],
  ["conclusion", "Conclusion · 研究結論", "text"],
  ["pitfalls_and_limitations", "Pitfalls & Limitations · 研究限制", "list"],
];
// Alias keys mirror the source groups in src/crawler.py. These are source
// groupings; an associated journal does not imply society authorship.
const JOURNALS = [
  ["Cephalalgia", "ihs", ["cephalalgia"]],
  ["Cephalalgia Reports", "ihs", ["cephalalgia reports"]],
  ["The Journal of Headache and Pain", "ehf", ["the journal of headache and pain", "j headache pain"]],
  ["Annals of Neurology", "aan", ["annals of neurology", "ann neurol"]],
  ["Neurology", "aan", ["neurology"]],
  ["Headache", "aan", ["headache"]],
  ["Japanese Journal of Headache", "jhs", ["japanese journal of headache"]],
  ["Neurology and Clinical Neuroscience", "jhs", ["neurology and clinical neuroscience", "neurol clin neurosci"]],
  ["Internal Medicine", "jhs", ["internal medicine", "intern med"]],
  ["Rinsho Shinkeigaku", "jhs", ["rinsho shinkeigaku"]],
  ["Acta Neurologica Taiwanica（台灣）", "ths", ["acta neurologica taiwanica", "acta neurol taiwan"]],
  ["Journal of the Chinese Medical Association（台灣）", "ths", ["journal of the chinese medical association", "j chin med assoc"]],
];
const SOCIETY_TAGS = {
  ihs: ["international headache society"],
  ehf: ["european headache federation"],
  aan: ["american neurological association", "american academy of neurology", "american headache society"],
  jhs: ["japanese headache society"],
  ths: ["taiwan headache society"],
};
const FILTERS = [
  ["all", "All · 全部"],
  ["ihs", "IHS / Cephalalgia"],
  ["ehf", "EHF / JHP"],
  ["aan", "ANA / AAN / AHS"],
  ["jhs", "JHS · 日本頭痛學會"],
  ["ths", "THS · 台灣頭痛學會"],
];
const FALLBACK_NOTICE = "Requires OPENAI_API_KEY for AI extraction";
// Exact PubMed trial types also mirror src/journal_metrics.py's admission policy.
const { CLINICAL_TRIAL_TYPES, NON_TRIAL_TYPES, isClinicalTrial } = DigestFilters;
const MODES = {
  fallback: ["原始摘要 · 尚未 AI 擷取", "bg-sky-50 text-sky-900"],
  structured: ["臨床結構摘要", "bg-emerald-50 text-emerald-900"],
};

function recordMode(paper) {
  if (paper.structured_summary.number_of_inclusion === FALLBACK_NOTICE) return "fallback";
  return "structured";
}
const byId = (id) => document.getElementById(id);
const state = {
  papers: [],
  policy: null,
  filter: "all",
  visible: 20,
  cached: false,
  loaded: false,
  facets: new Map(),
};
let pendingInstall;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}

function validText(value) {
  return typeof value === "string" && Boolean(value.trim());
}

function validatePapers(data) {
  if (!Array.isArray(data)) throw new Error("文獻資料格式有誤，請稍後重試。");
  const ids = new Set();
  for (const paper of data) {
    const meta = paper?.article_metadata;
    if (
      !meta ||
      ![
        "pmid_or_id",
        "title",
        "source_society_or_journal",
        "study_design",
        "doi",
        "publication_date",
      ].every((key) => validText(meta[key])) ||
      (meta.publication_date !== "Not reported" &&
        !/^\d{4}(-\d{2}(-\d{2})?)?$/.test(meta.publication_date)) ||
      Object.keys(paper).length !== 2 ||
      Object.keys(meta).length !== 6 ||
      !paper.structured_summary ||
      Object.keys(paper.structured_summary).length !== 7 ||
      FIELDS.some(([key, , type]) =>
        type === "list"
          ? !Array.isArray(paper.structured_summary[key]) ||
            !paper.structured_summary[key].length ||
            !paper.structured_summary[key].every(validText)
          : !validText(paper.structured_summary[key]),
      ) ||
      ids.has(meta.pmid_or_id)
    ) {
      throw new Error("部分文獻缺少必要欄位或含重複識別碼，請稍後重試。");
    }
    ids.add(meta.pmid_or_id);
  }
  return data;
}

function journalDefinition(source) {
  const key = source.split(/[|:(=]/, 1)[0].trim().toLowerCase();
  return JOURNALS.find(([, , aliases]) => aliases.includes(key));
}

function normalizeJournal(source) {
  return source.split("|", 1)[0].toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function validateJournalPolicy(policy) {
  const invalid = () => { throw new Error("期刊 JIF 資料無法核實，暫停顯示文獻。請稍後重試。"); };
  if (policy?.metric !== "Journal Impact Factor" || policy.threshold_exclusive !== 10 ||
      !Number.isInteger(policy.metric_year) || policy.metric_year < 2000 ||
      policy.jcr_release_year !== policy.metric_year + 1 ||
      !/^\d{4}-\d{2}-\d{2}$/.test(policy.verified_on) ||
      Number(policy.verified_on.slice(0, 4)) < policy.jcr_release_year ||
      !Array.isArray(policy.journals)) invalid();
  const aliases = new Map();
  for (const entry of policy.journals) {
    if (!validText(entry.journal) || !Array.isArray(entry.aliases) ||
        !entry.aliases.every(validText) || !Number.isInteger(entry.metric_year) || entry.metric_year < 2000 ||
        !safeUrl(entry.source_url) ||
        (entry.jif !== null && (typeof entry.jif !== "number" || !Number.isFinite(entry.jif) || entry.jif < 0))) invalid();
    for (const name of [entry.journal, ...entry.aliases]) {
      const key = normalizeJournal(name);
      if (!key || name.includes("|") || (aliases.has(key) && aliases.get(key) !== entry)) invalid();
      aliases.set(key, entry);
    }
  }
  return { ...policy, aliases };
}

function verifiedMetric(source) {
  const entry = state.policy?.aliases.get(normalizeJournal(source));
  return entry && entry.metric_year === state.policy.metric_year &&
    typeof entry.jif === "number" ? entry : null;
}

function trialDateEligible(value, today = new Date()) {
  const now = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()));
  const cutoff = new Date(now);
  cutoff.setUTCFullYear(cutoff.getUTCFullYear() - 10);
  if (cutoff.getUTCMonth() !== now.getUTCMonth()) cutoff.setUTCDate(0); // Leap-day anniversary.
  if (!/^\d{4}(-\d{2}(-\d{2})?)?$/.test(value)) return false;
  const padded = value + ({4: "-01-01", 7: "-01", 10: ""}[value.length]);
  const published = new Date(padded + "T00:00:00Z");
  return Number.isFinite(published.getTime()) && published.toISOString().slice(0, 10) === padded &&
    published >= cutoff && published <= now;
}

function eligiblePaper(paper) {
  const info = paper.article_metadata;
  const metric = verifiedMetric(info.source_society_or_journal);
  const trial = isClinicalTrial(info.study_design);
  return Boolean(metric && (metric.jif > 10 || (metric.jif < 10 && trial)) &&
    (!trial || trialDateEligible(info.publication_date)));
}

function canonicalJournal(source) {
  return journalDefinition(source)?.[0] || verifiedMetric(source)?.journal || source.split("|", 1)[0].trim();
}

function matchesFilter(paper, filter) {
  if (filter === "all") return true;
  const source = paper.article_metadata.source_society_or_journal;
  if (filter.startsWith("journal:")) return verifiedMetric(source)?.journal === filter.slice(8);
  const tags = source.toLowerCase().split("|").map((tag) => tag.trim());
  return journalDefinition(source)?.[1] === filter ||
    (SOCIETY_TAGS[filter] || []).some((tag) => tags.includes(tag));
}

function renderFilters() {
  const filters = FILTERS.filter(([key]) => key === "all" || state.papers.some((paper) => matchesFilter(paper, key)));
  const journals = [...new Set(state.papers.map((paper) => verifiedMetric(paper.article_metadata.source_society_or_journal)?.journal).filter(Boolean))].sort();
  filters.push(...journals.map((journal) => ["journal:" + journal, canonicalJournal(journal)]));
  if (!filters.some(([key]) => key === state.filter)) state.filter = "all";
  byId("journal-filters").replaceChildren(
    ...filters.map(([key, label]) => {
      const count = state.papers.filter((paper) =>
        matchesFilter(paper, key),
      ).length;
      const button = element("button", "journal-chip", label + " · " + count);
      button.dataset.filter = key;
      button.setAttribute("aria-pressed", String(state.filter === key));
      button.addEventListener("click", () => {
        state.filter = key;
        state.visible = 20;
        byId("journal-filters")
          .querySelectorAll("button")
          .forEach((item) =>
            item.setAttribute("aria-pressed", String(item === button)),
          );
        render();
      });
      return button;
    }),
  );
}

function renderFacetOptions() {
  const syndromeSelect = byId("syndrome-filter");
  const studySelect = byId("study-filter");
  const previousSyndrome = syndromeSelect.value;
  const previousStudy = studySelect.value;
  const matchers = syndromeCatalog ? DigestFilters.buildSyndromeMatchers(syndromeCatalog) : [];
  state.facets = new Map(state.papers.map(paper => [paper.article_metadata.pmid_or_id, {
    syndromes: syndromeCatalog ? DigestFilters.classifySyndromes(paper, matchers) : new Set(),
    studies: DigestFilters.classifyStudy(paper),
  }]));
  const counts = { syndromes: new Map(), studies: new Map() };
  for (const facets of state.facets.values()) for (const kind of Object.keys(counts)) {
    for (const key of facets[kind]) counts[kind].set(key, (counts[kind].get(key) || 0) + 1);
  }
  const option = (key, label, count) => {
    const node = element("option", "", `${label}（${count} 篇）`);
    node.value = key;
    return node;
  };
  studySelect.replaceChildren(option("all", "全部研究類型", state.papers.length));
  for (const label of new Set(DigestFilters.STUDY_TYPES.map(type => type[2]))) {
    const group = element("optgroup");
    group.label = label;
    for (const [key, name, category] of DigestFilters.STUDY_TYPES) if (category === label) {
      group.append(option(key, name, counts.studies.get(key) || 0));
    }
    studySelect.append(group);
  }
  studySelect.value = [...studySelect.options].some(o => o.value === previousStudy) ? previousStudy : "all";
  studySelect.disabled = !state.loaded;
  syndromeSelect.replaceChildren(option("all", "全部症候群", state.papers.length));
  if (syndromeCatalog) {
    for (const item of syndromeCatalog.groups) {
      const group = element("optgroup");
      group.label = `${item.code} · ${item.label}`;
      for (const entry of syndromeCatalog.entries.filter(e => e.code.split(".")[0] === item.code)) {
        group.append(option(entry.code, `${entry.code} · ${entry.code === item.code ? item.label + " · " : ""}${entry.name}`, counts.syndromes.get(entry.code) || 0));
      }
      syndromeSelect.append(group);
    }
    syndromeSelect.append(option("unclassified", "未明確配對症候群", counts.syndromes.get("unclassified") || 0));
  }
  syndromeSelect.value = [...syndromeSelect.options].some(o => o.value === previousSyndrome) ? previousSyndrome : "all";
  syndromeSelect.disabled = !syndromeCatalog || !state.loaded;
  if (!syndromeCatalog) syndromeSelect.options[0].textContent = "症候群目錄未就緒 · 請使用目錄重試";
}

function resetFilters() {
  byId("search").value = "";
  byId("syndrome-filter").value = "all";
  byId("study-filter").value = "all";
  state.filter = "all";
  state.visible = 20;
  renderFilters();
  render();
}

function paperCard(paper, index) {
  const info = paper.article_metadata;
  const mode = recordMode(paper);
  const details = element("details", "paper");
  details.dataset.pmid = info.pmid_or_id;
  details.open = index === 0;
  const heading = element("summary");
  const content = element("div");
  const meta = element("div", "paper-meta");
  meta.append(element("span", "source-badge", canonicalJournal(info.source_society_or_journal)));
  const metric = verifiedMetric(info.source_society_or_journal);
  if (metric) {
    const badge = element("a", "source-badge", `JIF ${metric.jif} · ${state.policy.metric_year}`);
    badge.href = metric.source_url;
    badge.target = "_blank";
    badge.rel = "noopener noreferrer";
    badge.title = "查看期刊 Impact Factor 來源";
    meta.append(badge);
    if (isClinicalTrial(info.study_design)) meta.append(element("span", "source-badge", "Clinical trial · PubMed 已標註"));
  }
  meta.append(element("span", "", info.publication_date || "發表日期未提供"));
  const [modeLabel, modeColor] = MODES[mode];
  meta.append(element("span", "record-mode " + modeColor, modeLabel));
  content.append(meta, element("h3", "paper-title", info.title));
  content.append(
    element(
      "p",
      "paper-journal",
      info.study_design + " · PMID / ID " + info.pmid_or_id,
    ),
  );
  const expand = element("span", "expand-icon", "+");
  expand.setAttribute("aria-hidden", "true");
  heading.append(
    element("span", "paper-number", String(index + 1).padStart(2, "0")),
    content,
    expand,
  );
  const body = element("div", "paper-content");
  if (mode === "fallback") {
    body.append(
      element(
        "p",
        "mb-4 rounded bg-sky-50 px-3 py-2 text-xs leading-relaxed text-sky-900",
        "尚未完成 AI 臨床擷取。「研究方法」與「研究結論」保留完整原始摘要，並非已分段判讀的結論。",
      ),
    );
  }
  const grid = element("dl", "clinical-grid");
  FIELDS.forEach(([key, label, type], position) => {
    const field = element("div", "clinical-field");
    const term = element("dt");
    term.append(
      element("span", "field-number", String(position + 1).padStart(2, "0")),
      document.createTextNode(label),
    );
    const value = element("dd");
    if (type === "list") {
      const list = element("ul", "m-0 list-disc space-y-2 pl-4");
      paper.structured_summary[key].forEach((item) =>
        list.append(element("li", "", item)),
      );
      value.append(list);
    } else {
      value.textContent = paper.structured_summary[key];
    }
    field.append(term, value);
    grid.append(field);
  });
  body.append(grid);
  const footer = element("div", "paper-footer");
  const source = element("a", "source-link", "閱讀原始來源 ↗");
  source.href = "https://pubmed.ncbi.nlm.nih.gov/" + encodeURIComponent(info.pmid_or_id) + "/";
  source.target = "_blank";
  source.rel = "noopener noreferrer";
  footer.append(
    source,
    element(
      "span",
      "summary-note",
      modeLabel,
    ),
  );
  if (info.doi !== "Not reported") {
    const doi = element(
      "a",
      "break-all text-xs text-stone-500",
      "DOI: " + info.doi,
    );
    doi.href = "https://doi.org/" + encodeURI(info.doi);
    doi.target = "_blank";
    doi.rel = "noopener noreferrer";
    footer.append(doi);
  }
  body.append(footer);
  details.append(heading, body);
  return details;
}

function render() {
  if (!state.loaded) return;
  const query = byId("search").value.trim().toLocaleLowerCase();
  const syndrome = byId("syndrome-filter").value;
  const study = byId("study-filter").value;
  const filtered = state.papers.filter(
    (paper) =>
      eligiblePaper(paper) &&
      matchesFilter(paper, state.filter) &&
      (syndrome === "all" || state.facets.get(paper.article_metadata.pmid_or_id)?.syndromes.has(syndrome)) &&
      (study === "all" || state.facets.get(paper.article_metadata.pmid_or_id)?.studies.has(study)) &&
      (!query ||
        [
          ...Object.values(paper.article_metadata),
          ...Object.values(paper.structured_summary).flat(),
        ]
          .filter(Boolean)
          .join(" ")
          .toLocaleLowerCase()
          .includes(query)),
  );
  const sort = byId("sort").value;
  filtered.sort((a, b) => {
    if (sort === "added") return 0; // Repository array is newest-added first.
    const left = a.article_metadata,
      right = b.article_metadata;
    if (left.publication_date === "Not reported")
      return right.publication_date !== "Not reported"
        ? 1
        : left.pmid_or_id.localeCompare(right.pmid_or_id);
    if (right.publication_date === "Not reported") return -1;
    const result = left.publication_date.localeCompare(right.publication_date);
    return (
      (sort === "oldest" ? result : -result) ||
      left.pmid_or_id.localeCompare(right.pmid_or_id)
    );
  });
  byId("paper-list").replaceChildren(
    ...filtered.slice(0, state.visible).map(paperCard),
  );
  byId("result-count").textContent = filtered.length + " 篇文獻";
  byId("load-more").hidden = filtered.length <= state.visible;
  byId("reset-filters").disabled = !query && state.filter === "all" && syndrome === "all" && study === "all";
  byId("empty").hidden = filtered.length !== 0;
  const hasData = state.papers.length > 0;
  byId("empty-title").textContent = hasData
    ? "找不到符合的文獻"
    : "目前沒有符合收錄條件的文獻";
  byId("empty-message").textContent = hasData
    ? "目前的症候群、研究類型、期刊與關鍵字組合沒有符合文獻。可調整條件或清除全部篩選。"
    : "收錄 JIF > 10 的文獻，或 JIF < 10 且 PubMed 已標註為 clinical trial 的文獻；皆須符合症候群目錄的研究主題範圍。";
  byId("clear-search").hidden = !hasData;
}

function connectionStatus() {
  byId("connection-status").hidden = !(state.cached || !navigator.onLine);
  byId("connection-status").textContent = state.cached
    ? "目前顯示此裝置快取的文獻，可能尚未包含最新更新。"
    : "目前離線；已載入的文獻仍可閱讀。";
}

async function loadPapers() {
  state.loaded = false;
  byId("loading").hidden = false;
  byId("error").hidden = true;
  byId("empty").hidden = true;
  byId("load-more").hidden = true;
  byId("mode-notice").hidden = true;
  byId("paper-list").setAttribute("aria-busy", "true");
  byId("paper-list").replaceChildren();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const [response, metricsResponse] = await Promise.all(
      ["./data/headache_papers.json", "./data/journal_metrics.json"].map((url) =>
        fetch(url, { cache: "no-store", signal: controller.signal })),
    );
    if (!response.ok || !metricsResponse.ok)
      throw new Error("文獻或 JIF 資料暫時無法取得，請稍後重試。");
    state.policy = validateJournalPolicy(await metricsResponse.json());
    state.papers = validatePapers(await response.json()).filter(eligiblePaper);
    byId("jif-notice").textContent = `${state.policy.metric_year} 年兩年制 JIF（JCR ${state.policy.jcr_release_year}）：> 10 可收錄；< 10 必須為 PubMed 已標註的 clinical trial。JIF = 10、未核實或年度不符均不收錄。`;
    state.loaded = true;
    state.cached = [response, metricsResponse].some((item) => item.headers.get("X-Digest-Cache") === "offline");
    byId("total-count").textContent =
      state.papers.length.toLocaleString("zh-TW");
    const dates = state.papers.map((paper) => paper.article_metadata.publication_date)
      .filter((value) => value !== "Not reported").sort();
    byId("last-updated").textContent = dates.at(-1) || "尚無發表日期";
    const fallback = state.papers.filter(
      (paper) => recordMode(paper) === "fallback",
    ).length;
    byId("mode-notice").hidden = !fallback;
    byId("mode-notice").textContent =
      [
        fallback ? fallback + " 篇原始摘要，尚未 AI 擷取" : "",
      ]
        .filter(Boolean)
        .join("；") + "。請依卡片標示區分資料模式。";
    renderFilters();
    renderFacetOptions();
    render();
    connectionStatus();
  } catch (error) {
    byId("error").hidden = false;
    byId("error-message").textContent =
      error.name === "AbortError"
        ? "連線逾時，請檢查網路後重試。"
        : error instanceof TypeError
          ? "請檢查網路連線後重試。"
          : error.message;
    byId("result-count").textContent = "";
  } finally {
    clearTimeout(timeout);
    byId("loading").hidden = true;
    byId("paper-list").setAttribute("aria-busy", "false");
  }
}

let syndromeCatalog = null;
let syndromeCached = false;

function validateSyndromes(data) {
  const invalid = () => { throw new Error("症候群目錄格式有誤，請稍後重試。"); };
  if (data?.edition !== "ICHD-3 beta (2013), user-supplied scope" ||
      !Array.isArray(data.entries) || !data.entries.length || !Array.isArray(data.groups) || !data.groups.length) invalid();
  const groups = new Set();
  for (const group of data.groups) {
    if (!/^A?(?:[1-9]|1[0-4])$/.test(group.code) || !validText(group.label) || groups.has(group.code)) invalid();
    groups.add(group.code);
  }
  const ids = new Set();
  for (const entry of data.entries) {
    if (!/^A?(?:[1-9]|1[0-4])(?:\.\d+)*$/.test(entry.code) || !validText(entry.name) ||
        !["syndrome", "migraine", "headache", "ocular_pain", "craniofacial_pain"].includes(entry.context) ||
        ids.has(entry.code) || !groups.has(entry.code.split(".")[0])) invalid();
    ids.add(entry.code);
  }
  if ([...groups].some((code) => !ids.has(code))) invalid();
  return data;
}

function renderSyndromes() {
  if (!syndromeCatalog) return;
  const query = byId("syndrome-search").value.trim().toLowerCase();
  const part = byId("syndrome-part").value;
  let count = 0;
  const sections = [];
  for (const group of syndromeCatalog.groups) {
    const appendix = group.code.startsWith("A");
    const number = Number(group.code);
    const groupPart = appendix ? "appendix" : number <= 4 ? "primary" : number <= 12 ? "secondary" : "cranial";
    if (part !== "all" && part !== groupPart) continue;
    const entries = syndromeCatalog.entries.filter((entry) => entry.code.split(".")[0] === group.code &&
      (!query || `${entry.code} ${entry.name} ${group.label}`.toLowerCase().includes(query)));
    if (!entries.length) continue;
    count += entries.length;
    const section = element("details", "syndrome-group");
    section.open = Boolean(query);
    section.append(element("summary", "", `${group.code} · ${group.label}（${entries.length}）`));
    const list = element("ul", "syndrome-list");
    for (const entry of entries) {
      const row = element("li", "syndrome-entry");
      row.dataset.code = entry.code;
      row.style.setProperty("--depth", Math.min(entry.code.split(".").length - 1, 4));
      row.append(element("span", "syndrome-code", entry.code), element("span", "", entry.name));
      list.append(row);
    }
    section.append(list);
    sections.push(section);
  }
  byId("syndrome-groups").replaceChildren(...sections);
  byId("syndrome-status").textContent = `${count} / ${syndromeCatalog.entries.length} 個分類條目（含主分類與附錄；非文獻篇數）` +
    (count ? "" : " · 找不到符合的症候群") + (syndromeCached ? " · 裝置快取" : "");
}

async function loadSyndromes() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  byId("syndrome-retry").hidden = true;
  try {
    const response = await fetch("./data/headache_syndromes.json", { cache: "no-store", signal: controller.signal });
    if (!response.ok) throw new Error("症候群目錄暫時無法取得。文獻書架仍可使用。");
    syndromeCatalog = validateSyndromes(await response.json());
    syndromeCached = response.headers.get("X-Digest-Cache") === "offline";
    renderSyndromes();
    renderFacetOptions();
    render();
  } catch (error) {
    syndromeCatalog = null;
    byId("syndrome-groups").replaceChildren();
    byId("syndrome-status").textContent = error.name === "AbortError" ? "讀取目錄逾時，請重試。" : error.message;
    byId("syndrome-retry").hidden = false;
    renderFacetOptions();
    render();
  } finally {
    clearTimeout(timeout);
  }
}

byId("syndrome-search").addEventListener("input", renderSyndromes);
byId("syndrome-part").addEventListener("change", renderSyndromes);
byId("syndrome-retry").addEventListener("click", loadSyndromes);
byId("search").addEventListener("input", () => {
  state.visible = 20;
  render();
});
byId("sort").addEventListener("change", render);
for (const id of ["syndrome-filter", "study-filter"]) byId(id).addEventListener("change", () => {
  state.visible = 20;
  render();
});
byId("reset-filters").addEventListener("click", resetFilters);
byId("load-more").addEventListener("click", () => {
  state.visible += 20;
  render();
});
byId("clear-search").addEventListener("click", () => {
  resetFilters();
  byId("search").focus();
});
byId("retry").addEventListener("click", loadPapers);
window.addEventListener("offline", connectionStatus);
window.addEventListener("online", loadPapers);
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  pendingInstall = event;
  byId("install").hidden = false;
});
byId("install").addEventListener("click", async () => {
  if (!pendingInstall) return;
  await pendingInstall.prompt();
  pendingInstall = null;
  byId("install").hidden = true;
});
window.addEventListener("appinstalled", () => {
  byId("install").hidden = true;
});

async function boot() {
  if ("serviceWorker" in navigator && window.isSecureContext) {
    try {
      await navigator.serviceWorker.register("./sw.js");
      if (!navigator.serviceWorker.controller) {
        await Promise.race([
          new Promise((resolve) =>
            navigator.serviceWorker.addEventListener(
              "controllerchange",
              resolve,
              { once: true },
            ),
          ),
          new Promise((resolve) => setTimeout(resolve, 1500)),
        ]);
      }
    } catch {
      /* The online reader also works without service worker support. */
    }
  }
  await Promise.all([loadPapers(), loadSyndromes()]);
}
boot();
