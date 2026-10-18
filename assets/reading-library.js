"use strict";

// Separate keys prevent a read toggle or another tab from overwriting a saved paper.
// The provider is lazy because accessing localStorage can itself throw.
(function (root) {
  function createLibrary(storageProvider, scope, validatePaper) {
    const prefix = `headache-digest:reading:v1:${scope}:`;
    const key = (kind, id) => prefix + kind + ":" + encodeURIComponent(id);
    const decodeSaved = (raw, id) => {
      const value = JSON.parse(raw);
      if (value?.version !== 1 || typeof value.savedAt !== "string" ||
          !Number.isFinite(Date.parse(value.savedAt)) ||
          value.paper?.article_metadata?.pmid_or_id !== id) throw new Error("Invalid saved paper");
      validatePaper(value.paper);
      return value;
    };
    return {
      prefix,
      load() {
        const read = new Set(), saved = new Map();
        let damaged = false;
        const storage = storageProvider();
        for (let i = 0; i < storage.length; i++) {
          const name = storage.key(i);
          if (!name?.startsWith(prefix)) continue;
          try {
            const suffix = name.slice(prefix.length);
            const split = suffix.indexOf(":");
            const kind = suffix.slice(0, split);
            if (!["read", "saved"].includes(kind)) continue;
            const id = decodeURIComponent(suffix.slice(split + 1));
            if (!id.trim()) throw new Error("Missing paper ID");
            const raw = storage.getItem(name);
            if (raw === null) continue;
            if (kind === "read") {
              if (raw !== "1") throw new Error("Invalid read marker");
              read.add(id);
            } else saved.set(id, decodeSaved(raw, id));
          } catch { damaged = true; } // A damaged entry must not hide healthy entries.
        }
        return { read, saved, damaged };
      },
      toggleRead(id) {
        const storage = storageProvider();
        const name = key("read", id);
        const read = storage.getItem(name) !== "1";
        if (read) storage.setItem(name, "1");
        else storage.removeItem(name);
        return read; // Only report success after the write succeeds.
      },
      toggleSaved(paper) {
        const storage = storageProvider();
        const id = paper.article_metadata.pmid_or_id;
        const name = key("saved", id);
        const current = storage.getItem(name);
        if (current !== null) {
          decodeSaved(current, id); // Do not silently discard a malformed backup.
          storage.removeItem(name);
          return false;
        }
        validatePaper(paper);
        storage.setItem(name, JSON.stringify({ version: 1, savedAt: new Date().toISOString(), paper }));
        return true;
      },
    };
  }
  function sortPapers(papers, read, mode = "unread") {
    return [...papers].sort((a, b) => {
      const left = a.article_metadata, right = b.article_metadata;
      if (mode === "unread") {
        const readingOrder = Number(read.has(left.pmid_or_id)) - Number(read.has(right.pmid_or_id));
        if (readingOrder) return readingOrder;
      }
      // Preserve feed order, or save order in the personal library.
      if (mode === "added") return 0;
      if (left.publication_date === "Not reported")
        return right.publication_date !== "Not reported" ? 1 : left.pmid_or_id.localeCompare(right.pmid_or_id);
      if (right.publication_date === "Not reported") return -1;
      const dateOrder = left.publication_date.localeCompare(right.publication_date);
      return (mode === "oldest" ? dateOrder : -dateOrder) || left.pmid_or_id.localeCompare(right.pmid_or_id);
    });
  }
  const api = { createLibrary, sortPapers };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.DigestLibrary = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
