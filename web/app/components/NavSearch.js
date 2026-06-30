"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { SearchIcon } from "./icons";

const PLACEHOLDER = "Search intel…  aave, restaking, base…";

export default function NavSearch() {
  const [val, setVal]               = useState("");
  const [loading, setLoading]       = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [suggests, setSuggests]     = useState([]);   // [{name, type, hasBrief}]
  const [active, setActive]         = useState(-1);    // highlighted suggestion index
  const [showSug, setShowSug]       = useState(false);
  const inputRef       = useRef(null);
  const mobileInputRef = useRef(null);
  const debounceRef    = useRef(null);
  const sugAbortRef    = useRef(null);
  const pickedRef      = useRef("");   // last value we set via a suggestion pick

  // Listen for search state feedback from BulletFeed
  useEffect(() => {
    const onLoading    = () => setLoading(true);
    const onDone       = () => setLoading(false);
    const onClearInput = () => { setVal(""); setLoading(false); setSuggests([]); setShowSug(false); };
    const onFocus      = () => inputRef.current?.focus();
    document.addEventListener("horyon:search-loading", onLoading);
    document.addEventListener("horyon:search-done",    onDone);
    document.addEventListener("horyon:clear-input",    onClearInput);
    document.addEventListener("horyon:focus-search",   onFocus);
    return () => {
      document.removeEventListener("horyon:search-loading", onLoading);
      document.removeEventListener("horyon:search-done",    onDone);
      document.removeEventListener("horyon:clear-input",    onClearInput);
      document.removeEventListener("horyon:focus-search",   onFocus);
    };
  }, []);

  // Close mobile overlay when clicking outside it
  useEffect(() => {
    if (!mobileOpen) return;
    function onPointerDown(e) {
      if (
        !e.target.closest(".mobile-search-overlay") &&
        !e.target.closest(".mobile-search-btn")
      ) {
        setMobileOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [mobileOpen]);

  // Close the suggestion dropdown on an outside click (desktop)
  useEffect(() => {
    if (!showSug) return;
    function onDown(e) {
      if (!e.target.closest(".nav-search") && !e.target.closest(".mobile-search-form")) {
        setShowSug(false);
      }
    }
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [showSug]);

  // Debounced entity typeahead. Suggestions come from /api/suggest (in-memory index,
  // no LLM/embed) so this is cheap to hit on every keystroke.
  useEffect(() => {
    const q = val.trim();
    if (q.length < 2 || q === pickedRef.current) {
      setSuggests([]); setActive(-1); setShowSug(false);
      return;
    }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      sugAbortRef.current?.abort();
      const ctrl = new AbortController();
      sugAbortRef.current = ctrl;
      try {
        const r = await fetch(`/api/suggest?q=${encodeURIComponent(q)}`, { signal: ctrl.signal });
        const data = await r.json();
        const list = Array.isArray(data.suggestions) ? data.suggestions : [];
        setSuggests(list);
        setActive(-1);
        setShowSug(list.length > 0);
      } catch {
        /* aborted or failed — leave dropdown as-is */
      }
    }, 140);
    return () => debounceRef.current && clearTimeout(debounceRef.current);
  }, [val]);

  const runSearch = useCallback((kw) => {
    if (!kw || loading) return;
    setShowSug(false);
    document.dispatchEvent(new CustomEvent("horyon:search", { detail: { keyword: kw } }));
    setMobileOpen(false);
  }, [loading]);

  // Picking a suggestion fills the box with the canonical name and searches it. The
  // /api/search free-text path resolves a known entity to its precomputed brief.
  const pickSuggestion = useCallback((name) => {
    pickedRef.current = name;
    setVal(name);
    setSuggests([]);
    setActive(-1);
    runSearch(name);
  }, [runSearch]);

  const handleSubmit = useCallback((e) => {
    e?.preventDefault?.();
    if (showSug && active >= 0 && suggests[active]) {
      pickSuggestion(suggests[active].name);
      return;
    }
    runSearch(val.trim());
  }, [val, showSug, active, suggests, pickSuggestion, runSearch]);

  const handleClear = useCallback(() => {
    setVal("");
    setSuggests([]);
    setShowSug(false);
    setActive(-1);
    document.dispatchEvent(new CustomEvent("horyon:clear-search"));
    inputRef.current?.focus();
  }, []);

  const onKeyNav = useCallback((e) => {
    if (e.key === "ArrowDown" && suggests.length) {
      e.preventDefault();
      setShowSug(true);
      setActive(i => (i + 1) % suggests.length);
    } else if (e.key === "ArrowUp" && suggests.length) {
      e.preventDefault();
      setActive(i => (i <= 0 ? suggests.length - 1 : i - 1));
    } else if (e.key === "Escape") {
      e.preventDefault();
      if (showSug) { setShowSug(false); setActive(-1); }
      else if (val) { handleClear(); }
      else { e.target.blur(); }
    }
  }, [suggests, showSug, val, handleClear]);

  function openMobile() {
    setMobileOpen(true);
    setTimeout(() => mobileInputRef.current?.focus(), 50);
  }

  // Rendered via a plain function call ({suggestList()}), NOT as <SuggestList/>.
  // Defining a component inside another and mounting it as an element gives it a new
  // identity every render, so React unmounts+remounts the whole dropdown on every
  // keystroke and every hover (setActive) — which drops the mousedown that picks an
  // item. Calling it inlines the JSX into NavSearch's own render: stable, no remount.
  function suggestList() {
    if (!showSug || !suggests.length) return null;
    return (
      <ul className="search-suggest" role="listbox">
        {suggests.map((s, i) => (
          <li
            key={s.name}
            role="option"
            aria-selected={i === active}
            className={`search-suggest-item${i === active ? " is-active" : ""}`}
            onMouseDown={(e) => { e.preventDefault(); pickSuggestion(s.name); }}
            onMouseEnter={() => setActive(i)}
          >
            <span className="search-suggest-name">{s.name}</span>
            <span className="search-suggest-meta">
              {s.type}{s.hasBrief && <span className="search-suggest-brief"> · brief</span>}
            </span>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <>
      {/* Desktop search bar — hidden on mobile via CSS */}
      <form className="search-bar nav-search" onSubmit={handleSubmit} role="search"
        aria-label="Search crypto intelligence">
        <span className="search-bar-icon"><SearchIcon size={13} /></span>
        <input
          ref={inputRef}
          type="search"
          className="search-input"
          placeholder={PLACEHOLDER}
          value={val}
          onChange={e => setVal(e.target.value)}
          onFocus={() => { if (suggests.length) setShowSug(true); }}
          onKeyDown={onKeyNav}
          aria-label="Search keyword"
          aria-autocomplete="list"
          aria-expanded={showSug}
          autoComplete="off"
          spellCheck={false}
        />
        <div className="search-bar-actions">
          {loading && <div className="spinner" aria-label="Searching…" />}
          {val && !loading && (
            <button type="button" className="search-clear-btn"
              onClick={handleClear} aria-label="Clear search">✕</button>
          )}
          <button type="submit" className="search-submit-btn"
            disabled={loading || !val.trim()} aria-label="Run search">↵</button>
        </div>
        {suggestList()}
      </form>

      {/* Mobile: icon button styled like theme toggle — visible only on mobile */}
      <button
        className={`mobile-search-btn${mobileOpen ? " is-active" : ""}`}
        onClick={openMobile}
        aria-label="Search"
        aria-expanded={mobileOpen}
      >
        <SearchIcon size={13} />
      </button>

      {/* Mobile search overlay — slides down below the header */}
      {mobileOpen && (
        <div className="mobile-search-overlay">
          <form className="mobile-search-form" onSubmit={handleSubmit}>
            <span className="search-bar-icon"><SearchIcon size={13} /></span>
            <input
              ref={mobileInputRef}
              type="search"
              className="search-input"
              placeholder={PLACEHOLDER}
              value={val}
              onChange={e => setVal(e.target.value)}
              onKeyDown={onKeyNav}
              aria-autocomplete="list"
              autoComplete="off"
              spellCheck={false}
            />
            <div className="search-bar-actions">
              {loading && <div className="spinner" aria-label="Searching…" />}
              {val && !loading && (
                <button type="submit" className="search-submit-btn"
                  disabled={loading || !val.trim()}>↵</button>
              )}
              <button type="button" className="search-clear-btn"
                onClick={() => setMobileOpen(false)} aria-label="Close search">✕</button>
            </div>
            {suggestList()}
          </form>
        </div>
      )}
    </>
  );
}
