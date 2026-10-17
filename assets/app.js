"use strict";

const FIELDS = [
  ["methods", "Methods · 研究方法", "text"],
  ["mechanism_of_action_moa", "MOA · 作用機轉", "text"],
  ["number_of_inclusion", "Number of Inclusion · 納入人數", "text"],
  ["inclusion_criteria", "Inclusion Criteria · 納入條件", "list"],
  ["exclusion_criteria", "Exclusion Criteria · 排除條件", "list"],
  ["conclusion", "Conclusion · 研究結論", "text"],
  ["pitfalls_and_limitations", "Pitfalls & Limitations · 研究限制", "list"],
];
const JOURNALS = [
  "Cephalalgia",
  "The Journal of Headache and Pain",
  "Headache",
  "Neurology",
  "Annals of Neurology",
  "Acta Neurologica Taiwanica",
  "Internal medicine",
];
const FILTERS = [
  ["all", "All · 全部"],
  ["ihs", "IHS / Cephalalgia"],
  ["ehf", "EHF / JHP"],
  ["aan", "AAN / ANA"],
  ["jhs", "JHS"],
  ["ths", "THS"],
  ["headache", "Headache"],
];
const SEED_IDS = new Set(["29471679", "24144382", "23390177"]);
const FALLBACK_NOTICE = "Requires OPENAI_API_KEY for AI extraction";
const MODES = {
  seed: ["來源核對範例", "bg-amber-50 text-amber-900"],
  fallback: ["原始摘要 · 尚未 AI 擷取", "bg-sky-50 text-sky-900"],
  structured: ["臨床結構摘要", "bg-emerald-50 text-emerald-900"],
};

function recordMode(paper) {
  if (paper.structured_summary.number_of_inclusion === FALLBACK_NOTICE) return "fallback";
  return SEED_IDS.has(paper.article_metadata.pmid_or_id) ? "seed" : "structured";
}
const byId = (id) => document.getElementById(id);
const state = {
  papers: [],
  filter: "all",
  visible: 20,
  cached: false,
  loaded: false,
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

function canonicalJournal(name) {
  // PubMed journal titles can include subtitles after a colon.
  const journal = name.split("|", 1)[0].trim();
  const short = journal.split(":", 1)[0].trim().toLowerCase();
  return JOURNALS.find((item) => item.toLowerCase() === short) || journal;
}

function matchesFilter(paper, filter) {
  if (filter === "all") return true;
  const source = paper.article_metadata.source_society_or_journal.toLowerCase();
  const journal = canonicalJournal(source);
  switch (filter) {
    case "ihs": return journal === "Cephalalgia" || source.includes("international headache society");
    case "ehf": return journal === "The Journal of Headache and Pain" || source.includes("european headache federation");
    case "aan": return ["Neurology", "Annals of Neurology"].includes(journal) || /american (academy of neurology|neurological association)/.test(source);
    case "jhs": return source.includes("japanese headache society");
    case "ths": return journal === "Acta Neurologica Taiwanica" || source.includes("taiwan headache society");
    case "headache": return journal === "Headache";
    default: return false;
  }
}

function renderFilters() {
  byId("journal-filters").replaceChildren(
    ...FILTERS.map(([key, label]) => {
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
  if (mode === "seed") {
    body.append(
      element(
        "p",
        "mb-4 rounded bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-900",
        "歷史範例：依可核對的 PubMed 摘要整理，請依發表年份評估適用性。",
      ),
    );
  } else if (mode === "fallback") {
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
  const filtered = state.papers.filter(
    (paper) =>
      matchesFilter(paper, state.filter) &&
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
  byId("empty").hidden = filtered.length !== 0;
  const hasData = state.papers.length > 0;
  byId("empty-title").textContent = hasData
    ? "找不到符合的文獻"
    : "文獻書架準備好了";
  byId("empty-message").textContent = hasData
    ? "試試其他關鍵字，或切換期刊與學會。"
    : "文獻收錄後，會在這裡顯示。";
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
    const response = await fetch("./data/headache_papers.json", {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok)
      throw new Error("資料暫時無法取得（HTTP " + response.status + "）。");
    state.papers = validatePapers(await response.json());
    state.loaded = true;
    state.cached = response.headers.get("X-Digest-Cache") === "offline";
    byId("total-count").textContent =
      state.papers.length.toLocaleString("zh-TW");
    const dates = state.papers.map((paper) => paper.article_metadata.publication_date)
      .filter((value) => value !== "Not reported").sort();
    byId("last-updated").textContent = dates.at(-1) || "尚無發表日期";
    const seeds = state.papers.filter(
      (paper) => recordMode(paper) === "seed",
    ).length;
    const fallback = state.papers.filter(
      (paper) => recordMode(paper) === "fallback",
    ).length;
    byId("mode-notice").hidden = !(seeds || fallback);
    byId("mode-notice").textContent =
      [
        seeds ? seeds + " 篇來源核對歷史範例，並非最新文獻" : "",
        fallback ? fallback + " 篇原始摘要，尚未 AI 擷取" : "",
      ]
        .filter(Boolean)
        .join("；") + "。請依卡片標示區分資料模式。";
    renderFilters();
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

byId("search").addEventListener("input", () => {
  state.visible = 20;
  render();
});
byId("sort").addEventListener("change", render);
byId("load-more").addEventListener("click", () => {
  state.visible += 20;
  render();
});
byId("clear-search").addEventListener("click", () => {
  byId("search").value = "";
  byId("journal-filters").querySelector('[data-filter="all"]').click();
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
  await loadPapers();
}
boot();
