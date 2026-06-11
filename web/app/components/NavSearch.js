"use client";

import { useState, useRef, useEffect, useCallback } from "react";

function SearchIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none"
      stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <circle cx="6.5" cy="6.5" r="4.5" />
      <line x1="10.5" y1="10.5" x2="14" y2="14" />
    </svg>
  );
}

const PLACEHOLDER = "Search intel…  aave, restaking, base…";

export default function NavSearch() {
  const [val, setVal]               = useState("");
  const [loading, setLoading]       = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const inputRef       = useRef(null);
  const mobileInputRef = useRef(null);

  // Listen for search state feedback from BulletFeed
  useEffect(() => {
    const onLoading    = () => setLoading(true);
    const onDone       = () => setLoading(false);
    const onClearInput = () => { setVal(""); setLoading(false); };
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

  const handleSubmit = useCallback((e) => {
    e?.preventDefault?.();
    const kw = val.trim();
    if (!kw || loading) return;
    document.dispatchEvent(new CustomEvent("horyon:search", { detail: { keyword: kw } }));
    setMobileOpen(false);
  }, [val, loading]);

  const handleClear = useCallback(() => {
    setVal("");
    document.dispatchEvent(new CustomEvent("horyon:clear-search"));
    inputRef.current?.focus();
  }, []);

  function openMobile() {
    setMobileOpen(true);
    setTimeout(() => mobileInputRef.current?.focus(), 50);
  }

  return (
    <>
      {/* Desktop search bar — hidden on mobile via CSS */}
      <form className="search-bar nav-search" onSubmit={handleSubmit} role="search"
        aria-label="Search crypto intelligence">
        <span className="search-bar-icon"><SearchIcon /></span>
        <input
          ref={inputRef}
          type="search"
          className="search-input"
          placeholder={PLACEHOLDER}
          value={val}
          onChange={e => setVal(e.target.value)}
          onKeyDown={e => {
            if (e.key === "Escape") {
              e.preventDefault();
              if (val) { handleClear(); } else { e.target.blur(); }
            }
          }}
          aria-label="Search keyword"
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
      </form>

      {/* Mobile: icon button styled like theme toggle — visible only on mobile */}
      <button
        className={`mobile-search-btn${mobileOpen ? " is-active" : ""}`}
        onClick={openMobile}
        aria-label="Search"
        aria-expanded={mobileOpen}
      >
        <SearchIcon />
      </button>

      {/* Mobile search overlay — slides down below the header */}
      {mobileOpen && (
        <div className="mobile-search-overlay">
          <form className="mobile-search-form" onSubmit={handleSubmit}>
            <span className="search-bar-icon"><SearchIcon /></span>
            <input
              ref={mobileInputRef}
              type="search"
              className="search-input"
              placeholder={PLACEHOLDER}
              value={val}
              onChange={e => setVal(e.target.value)}
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
          </form>
        </div>
      )}
    </>
  );
}
