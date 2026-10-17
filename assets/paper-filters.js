"use strict";

// Literature facets only: these never change admission policy or assign a diagnosis.
// Kept DOM-free so the same classification can be tested without a browser/API.
const DigestFilters = (() => {
  const CLINICAL_TRIAL_TYPES = new Set([
    "clinical trial", "clinical trial, phase i", "clinical trial, phase ii",
    "clinical trial, phase iii", "clinical trial, phase iv", "controlled clinical trial",
    "randomized controlled trial", "pragmatic clinical trial", "adaptive clinical trial", "equivalence trial",
  ]);
  const NON_TRIAL_TYPES = new Set([
    "clinical trial protocol", "clinical trial, veterinary", "observational study",
    "review", "systematic review", "meta-analysis",
  ]);
  const typesOf = (design) => new Set(design.toLowerCase().split(";").map(x => x.trim().replace(/\s+/g, " ")));
  function isClinicalTrial(design) {
    const types = typesOf(design);
    return [...types].some(x => CLINICAL_TRIAL_TYPES.has(x)) && ![...types].some(x => NON_TRIAL_TYPES.has(x));
  }

  // A paper may have multiple designs/attributes. No "nonrandomized" inference
  // is made merely because a Clinical Trial lacks an RCT publication type.
  const STUDY_TYPES = [
    ["clinical-trial", "Clinical trials · 臨床試驗（PubMed）", "介入研究"],
    ["rct", "Randomized controlled trial · 隨機對照試驗", "介入研究"],
    ["nonrandomized", "Nonrandomized trial · 非隨機介入試驗", "介入研究"],
    ...["I", "II", "III", "IV"].map(n => ["phase-" + n.toLowerCase(), "Phase " + n + " · 第 " + n + " 期臨床試驗", "介入研究"]),
    ["crossover", "Crossover trial · 交叉試驗", "介入研究"],
    ["pilot", "Pilot / feasibility study · 先導／可行性研究", "介入研究"],
    ["case-report", "Case report · 病例報告", "觀察與描述研究"],
    ["case-series", "Case series · 病例系列", "觀察與描述研究"],
    ["cohort", "Cohort study · 世代研究", "觀察與描述研究"],
    ["case-control", "Case-control study · 病例對照研究", "觀察與描述研究"],
    ["cross-sectional", "Cross-sectional study · 橫斷研究", "觀察與描述研究"],
    ["observational", "Observational study · 觀察性研究", "觀察與描述研究"],
    ["systematic-review", "Systematic review · 系統性回顧", "文獻整合與建議"],
    ["meta-analysis", "Meta-analysis · 統合分析", "文獻整合與建議"],
    ["scoping-review", "Scoping review · 範疇性回顧", "文獻整合與建議"],
    ["review", "Review · 回顧文獻", "文獻整合與建議"],
    ["guideline", "Guideline · 臨床指引", "文獻整合與建議"],
    ["consensus", "Consensus / Delphi · 共識研究", "文獻整合與建議"],
    ["protocol", "Study protocol · 研究計畫書", "其他設計與研究特徵"],
    ["diagnostic", "Diagnostic accuracy study · 診斷準確度研究", "其他設計與研究特徵"],
    ["validation", "Validation study · 效度驗證研究", "其他設計與研究特徵"],
    ["qualitative", "Qualitative study · 質性研究", "其他設計與研究特徵"],
    ["mixed-methods", "Mixed methods · 混合方法研究", "其他設計與研究特徵"],
    ["economic", "Economic evaluation · 經濟評估", "其他設計與研究特徵"],
    ["secondary-analysis", "Secondary / post-hoc analysis · 次級／事後分析", "其他設計與研究特徵"],
    ["comparative", "Comparative study · 比較研究", "其他設計與研究特徵"],
    ["multicenter", "Multicenter study · 多中心研究", "其他設計與研究特徵"],
    ["preclinical", "Preclinical study · 臨床前研究", "其他設計與研究特徵"],
    ["editorial", "Editorial / commentary · 社論／評論", "其他設計與研究特徵"],
    ["letter", "Letter · 通訊", "其他設計與研究特徵"],
    ["unclassified", "未明確標示研究類型", "其他設計與研究特徵"],
  ];
  const normalize = text => text.normalize("NFKC").toLowerCase().replace(/[‐‑‒–—−-]/g, " ").replace(/\s+/g, " ").trim();
  function positiveMatch(text, pattern) {
    const matches = text.matchAll(new RegExp(pattern.source, "gi"));
    for (const match of matches) {
      const before = text.slice(Math.max(0, match.index - 60), match.index);
      const after = text.slice(match.index + match[0].length, match.index + match[0].length + 45);
      if (/(?:\b(?:no|not|without|excluding|excluded|rather than)\s+(?:(?:a|an|any|evidence of|patients? with|history of)\s+)*|(?:排除|未納入|無|並非|不是|非)(?:患有|具有|採用|進行|的)?)$/i.test(before)) continue;
      if (/^\s+(?:was|were|is)\s+not\s+(?:performed|conducted|included|reported)/i.test(after)) continue;
      return true;
    }
    return false;
  }
  function classifyStudy(paper) {
    const meta = paper.article_metadata;
    const types = typesOf(meta.study_design);
    const text = normalize(`${meta.title}\n${paper.structured_summary.methods}`);
    const result = new Set();
    const add = (id, tags, regex) => {
      if (tags.some(t => types.has(t)) || (regex && positiveMatch(text, regex))) result.add(id);
    };
    add("systematic-review", ["systematic review"], /\bsystematic review\b|系統[性式]?(?:文獻)?回顧/);
    add("meta-analysis", ["meta-analysis"], /\bmeta analys(?:is|es)\b|統合分析|薈萃分析/);
    add("scoping-review", ["scoping review"], /\bscoping review\b|範疇[性式]?回顧/);
    add("review", ["review"], /\bnarrative review\b|敘述[性式]?回顧/);
    add("guideline", ["guideline", "practice guideline"], /\bclinical (?:practice )?guideline\b|臨床(?:實務)?(?:指引|指南)/);
    add("consensus", ["consensus development conference", "consensus development conference, nih"], /\b(?:consensus (?:statement|recommendations?|study)|delphi (?:study|method|process))\b|共識(?:聲明|研究)|德爾菲/);
    add("protocol", ["clinical trial protocol"], /\b(?:study|trial) protocol\b|研究計畫書|試驗方案/);
    add("editorial", ["editorial", "comment"], /\b(?:editorial|commentary)\b/);
    add("letter", ["letter"], null);
    if (["systematic-review", "meta-analysis", "scoping-review"].some(id => result.has(id))) result.add("review");
    // Reviews/protocols describe other studies or future recruitment. Do not
    // relabel them as completed primary studies based on those descriptions.
    if (!["review", "guideline", "consensus", "protocol", "editorial", "letter"].some(id => result.has(id))) {
      if (isClinicalTrial(meta.study_design)) {
        result.add("clinical-trial");
        if (types.has("randomized controlled trial")) result.add("rct");
        for (const n of ["i", "ii", "iii", "iv"]) if (types.has("clinical trial, phase " + n)) result.add("phase-" + n);
      }
      add("case-report", ["case reports"], /\bcase report\b(?!\s+forms?)|病例報告(?!表)|個案報告(?!表)/);
      add("case-series", [], /\bcase series\b|病例系列|個案系列/);
      add("cohort", ["cohort study", "cohort studies"], /\bcohort stud(?:y|ies)\b|世代研究|隊列研究|群組研究/);
      add("case-control", ["case-control study", "case-control studies"], /\bcase control stud(?:y|ies)\b|病例[－對]?照研究|病例對照研究/);
      add("cross-sectional", ["cross-sectional study", "cross-sectional studies"], /\bcross sectional (?:study|studies|analysis|survey)\b|橫斷(?:面|性)?研究/);
      add("observational", ["observational study"], /\bobservational stud(?:y|ies)\b|觀察[性式]研究/);
      if (["cohort", "case-control", "cross-sectional"].some(id => result.has(id))) result.add("observational");
      add("nonrandomized", [], /\bnon ?randomi[sz]ed (?:controlled )?(?:trial|study)\b|非隨機(?:對照)?(?:試驗|介入研究)/);
      add("crossover", [], /\bcross ?over (?:trial|study|design)\b|交叉(?:試驗|研究|設計)/);
      add("pilot", [], /\b(?:pilot|feasibility) (?:study|trial)\b|先導(?:性)?(?:研究|試驗)|可行性(?:研究|試驗)/);
      add("diagnostic", [], /\bdiagnostic accuracy (?:study|analysis)\b|診斷準確(?:度|性)研究/);
      add("validation", ["validation study"], /\bvalidation stud(?:y|ies)\b|效度驗證研究|驗證研究/);
      add("qualitative", [], /\bqualitative (?:study|research|analysis)\b|質性(?:研究|分析)/);
      add("mixed-methods", [], /\bmixed methods?\b|混合方法研究/);
      add("economic", [], /\b(?:cost effectiveness|cost utility|economic) (?:analysis|evaluation)\b|成本效益分析|成本效用分析|經濟評估/);
      add("secondary-analysis", [], /\b(?:post hoc|secondary|pooled) analys(?:is|es)\b|事後分析|次級分析|次要分析|合併分析/);
      add("comparative", ["comparative study"], /\bcomparative stud(?:y|ies)\b|比較研究/);
      add("multicenter", ["multicenter study"], /\bmulti ?cent(?:er|re)\b|多中心/);
      add("preclinical", [], /\bpreclinical (?:study|studies|research)\b|臨床前研究|動物實驗/);
    }
    if (!result.size) result.add("unclassified");
    return result;
  }

  // Common bilingual names and unambiguous syndrome abbreviations supplement
  // all 368 catalog names. A cause alone is not an attributed-headache diagnosis.
  const SYNDROME_ALIASES = {
    "1": ["偏頭痛"], "1.1": ["無先兆偏頭痛", "無預兆偏頭痛"],
    "1.2": ["有先兆偏頭痛", "有預兆偏頭痛"], "1.2.2": ["腦幹先兆偏頭痛"],
    "1.2.3": ["偏癱型偏頭痛"], "1.2.3.1": ["FHM"], "1.2.3.1.1": ["FHM1"],
    "1.2.3.1.2": ["FHM2"], "1.2.3.1.3": ["FHM3"], "1.3": ["慢性偏頭痛"],
    "1.6.1.1": ["cyclic vomiting syndrome", "週期性嘔吐症候群"], "1.6.1.2": ["腹部偏頭痛"],
    "2": ["TTH", "緊縮型頭痛", "緊張型頭痛"], "2.3": ["慢性緊縮型頭痛", "慢性緊張型頭痛"],
    "3": ["TACs"], "3.1": ["叢集性頭痛", "叢集頭痛", "群發性頭痛"],
    "3.1.1": ["發作性叢集性頭痛"], "3.1.2": ["慢性叢集性頭痛"],
    "3.2": ["陣發性偏側頭痛"], "3.3": ["SUNHA"], "3.3.1": ["SUNCT"], "3.3.2": ["SUNA"],
    "3.4": ["持續性偏側頭痛"], "4.1": ["primary cough induced headache", "原發性咳嗽頭痛"],
    "4.2": ["primary exertional headache", "原發性運動頭痛"], "4.7": ["原發性刺痛性頭痛"],
    "4.8": ["錢幣狀頭痛"], "4.9": ["睡眠性頭痛"], "4.10": ["NDPH", "新發每日持續性頭痛"],
    "5": ["post traumatic headache", "posttraumatic headache", "headache after traumatic brain injury", "外傷後頭痛"],
    "7.2.1": ["postdural puncture headache", "post dural puncture headache", "硬腦膜穿刺後頭痛"],
    "8.2": ["MOH", "藥物過度使用性頭痛", "藥物過度使用頭痛", "藥物過量使用頭痛"],
    "10.2": ["haemodialysis related headache", "hemodialysis related headache", "透析頭痛"],
    "10.5": ["fasting headache", "fasting induced headache", "禁食性頭痛"],
    "10.6": ["心臟性頭痛"], "11.2.1": ["頸因性頭痛"],
    "13.1": ["三叉神經痛"], "13.1.2": ["trigeminal neuropathic pain", "疼痛性三叉神經病變"],
    "13.2": ["舌咽神經痛"], "13.3": ["geniculate neuralgia", "nervus intermedius neuralgia"],
    "13.4": ["枕神經痛"], "13.7": ["Tolosa Hunt"], "13.8": ["Raeder syndrome", "Raeder's syndrome"],
    "13.10": ["BMS", "glossodynia", "stomatodynia", "灼口症候群"],
    "13.11": ["PIFP", "持續性特發性顏面痛", "持續性特發性面部疼痛"],
    "13.12.2": ["CPSP"], "A1.1.1": ["純月經性偏頭痛"],
    "A1.1.2": ["月經相關偏頭痛"], "A1.6.6": ["前庭性偏頭痛"],
  };
  function phrasePattern(phrase) {
    const escaped = normalize(phrase).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const english = /^[\x00-\x7F]+$/.test(phrase);
    return new RegExp(english ? "\\b" + escaped + (/(?:headache|migraine|cephalalgia)$/.test(escaped) ? "s?" : "") + "\\b" : escaped, "i");
  }
  function buildSyndromeMatchers(catalog) {
    const baseNames = new Set(catalog.entries.filter(e => !e.code.startsWith("A")).map(e => normalize(e.name.replace(/\s*\([^)]*\)/g, ""))));
    return catalog.entries.map(entry => {
      const name = entry.name.replace(/\s*\([^)]*\)/g, "").trim();
      const duplicateAppendix = entry.code.startsWith("A") && baseNames.has(normalize(name));
      // Ordinary migraine must not imply study of alternative ICHD criteria.
      const names = [...new Set([entry.name, name, ...(SYNDROME_ALIASES[entry.code] || [])])];
      return { ...entry, duplicateAppendix, patterns: names.map(phrasePattern) };
    });
  }
  function classifySyndromes(paper, matchers) {
    // Exclusions, limitations and conclusions often mention unrelated diseases.
    const text = normalize(`${paper.article_metadata.title}\n${paper.structured_summary.methods}\n${paper.structured_summary.inclusion_criteria.join("\n")}`);
    const result = new Set();
    const codes = new Set(matchers.map(entry => entry.code));
    for (const entry of matchers) {
      if (entry.duplicateAppendix && !/\balternative criteria\b|\bappendix\b|附錄|替代(?:性)?標準/.test(text)) continue;
      if (!entry.patterns.some(pattern => positiveMatch(text, pattern))) continue;
      if (entry.context === "migraine" && !/\bmigraines?\b|偏頭痛/.test(text)) continue;
      if (entry.context === "headache" && !/\bheadaches?\b|\bmigraines?\b|頭痛/.test(text)) continue;
      if (entry.context === "ocular_pain" && !/\bpain(?:ful)?\b|疼痛|眼痛/.test(text)) continue;
      if (entry.context === "craniofacial_pain" && !/\b(?:facial|face|craniofacial|orofacial|trigeminal)\b|顏面|面部|三叉/.test(text)) continue;
      let code = entry.code;
      while (code) {
        if (codes.has(code)) result.add(code);
        code = code.includes(".") ? code.slice(0, code.lastIndexOf(".")) : "";
      }
    }
    if (!result.size) result.add("unclassified");
    return result;
  }
  return { CLINICAL_TRIAL_TYPES, NON_TRIAL_TYPES, isClinicalTrial, STUDY_TYPES,
    classifyStudy, buildSyndromeMatchers, classifySyndromes };
})();

if (typeof module !== "undefined" && module.exports) module.exports = DigestFilters;
