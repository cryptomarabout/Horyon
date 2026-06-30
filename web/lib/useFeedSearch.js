import { useState, useCallback, useEffect } from "react";

// Owns the daily feed's free-text / entity search: the result + synth state, the
// zero-LLM `/api/search` calls (an entity search runs a fast brief pass, then a
// slower analyst-synth pass), the NavSearch event-bus wiring, and the `searchProp`
// the RightPanel consumes. The feed passes two callbacks so search can drive the
// shared panel without owning selection: `onSearchOpen` clears the current
// selection + opens the panel; `onSearchClear` closes it iff nothing else is open.
export default function useFeedSearch({ onSearchOpen, onSearchClear }) {
  const [searchQuery,  setSearchQuery]  = useState("");
  const [searchState,  setSearchState]  = useState("idle");
  const [searchResult, setSearchResult] = useState("");
  const [searchSrcs,   setSearchSrcs]   = useState(0);
  const [searchSynth,      setSearchSynth]      = useState("");
  const [searchSynthState, setSearchSynthState] = useState("none");

  const handleSearch = useCallback(async (kw, opts = {}) => {
    setSearchQuery(kw);
    setSearchState("loading");
    setSearchResult("");
    setSearchSrcs(0);
    setSearchSynth("");
    setSearchSynthState("none");
    onSearchOpen();
    document.dispatchEvent(new CustomEvent("horyon:search-loading"));

    if (opts.entity) {
      try {
        const r = await fetch("/api/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ keyword: kw, entity: true, mode: "feed" }),
        });
        const data = await r.json();
        setSearchResult(r.ok ? (data.content || "No recent items.") : (data.error || "Search failed."));
        setSearchSrcs(data.sources ?? 0);
        setSearchState(r.ok ? "done" : "error");
      } catch {
        setSearchResult("Could not reach search service.");
        setSearchState("error");
      }
      document.dispatchEvent(new CustomEvent("horyon:search-done"));

      setSearchSynthState("loading");
      try {
        const r2 = await fetch("/api/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ keyword: kw, entity: true, mode: "synth" }),
        });
        const d2 = await r2.json();
        const synth = (r2.ok && d2.content) ? d2.content : "";
        setSearchSynth(synth);
        setSearchSynthState(synth ? "done" : "none");
      } catch {
        setSearchSynthState("none");
      }
      return;
    }

    try {
      const r = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keyword: kw, entity: false }),
      });
      const data = await r.json();
      if (!r.ok) {
        setSearchResult(data.error || "Search failed.");
        setSearchState("error");
      } else {
        setSearchResult(data.content || "No results.");
        setSearchSrcs(data.sources ?? 0);
        setSearchState("done");
      }
    } catch {
      setSearchResult("Could not reach search service.");
      setSearchState("error");
    } finally {
      document.dispatchEvent(new CustomEvent("horyon:search-done"));
    }
  }, [onSearchOpen]);

  const handleClearSearch = useCallback(() => {
    setSearchQuery("");
    setSearchState("idle");
    setSearchResult("");
    setSearchSrcs(0);
    setSearchSynth("");
    setSearchSynthState("none");
    document.dispatchEvent(new CustomEvent("horyon:clear-input"));
    onSearchClear();
  }, [onSearchClear]);

  // NavSearch (header) talks to the feed over a document event bus.
  useEffect(() => {
    const onSearch = (e) => handleSearch(e.detail.keyword);
    const onClear  = () => handleClearSearch();
    document.addEventListener("horyon:search", onSearch);
    document.addEventListener("horyon:clear-search", onClear);
    return () => {
      document.removeEventListener("horyon:search", onSearch);
      document.removeEventListener("horyon:clear-search", onClear);
    };
  }, [handleSearch, handleClearSearch]);

  const searchProp = searchQuery
    ? { keyword: searchQuery, state: searchState, result: searchResult, sources: searchSrcs,
        synth: searchSynth, synthState: searchSynthState, onClose: handleClearSearch }
    : null;

  return { searchQuery, searchProp, handleSearch, handleClearSearch };
}
