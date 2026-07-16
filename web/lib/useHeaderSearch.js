"use client";

import { useState, useCallback, useEffect } from "react";

// Wires the global header NavSearch (event bus) to a RightPanel SearchPanel.
// Shared by the Narratives + Weekly views so search works on every route.
export default function useHeaderSearch() {
  const [query,  setQuery]  = useState("");
  const [state,  setState]  = useState("idle");
  const [result, setResult] = useState("");
  const [sources, setSources] = useState(0);
  const [asOf, setAsOf] = useState(null);
  const [facts, setFacts] = useState(null);

  const run = useCallback(async (kw) => {
    setQuery(kw); setState("loading"); setResult(""); setSources(0); setAsOf(null); setFacts(null);
    document.dispatchEvent(new CustomEvent("horyon:search-loading"));
    try {
      const r = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keyword: kw }),
      });
      const data = await r.json();
      if (!r.ok) { setResult(data.error || "Search failed."); setState("error"); }
      else {
        setResult(data.content || "No results.");
        setSources(data.sources ?? 0);
        setAsOf(data.cached ? (data.asOf ?? null) : null);
        setFacts(data.facts ?? null);
        setState("done");
      }
    } catch {
      setResult("Could not reach search service."); setState("error");
    } finally {
      document.dispatchEvent(new CustomEvent("horyon:search-done"));
    }
  }, []);

  const clear = useCallback(() => {
    setQuery(""); setState("idle"); setResult(""); setSources(0); setAsOf(null); setFacts(null);
    document.dispatchEvent(new CustomEvent("horyon:clear-input"));
  }, []);

  useEffect(() => {
    const onSearch = (e) => run(e.detail.keyword);
    const onClear  = () => clear();
    document.addEventListener("horyon:search", onSearch);
    document.addEventListener("horyon:clear-search", onClear);
    return () => {
      document.removeEventListener("horyon:search", onSearch);
      document.removeEventListener("horyon:clear-search", onClear);
    };
  }, [run, clear]);

  const searchProp = query
    ? { keyword: query, state, result, sources, asOf, facts, onClose: clear }
    : null;

  return { searchProp, clearSearch: clear };
}
