"use client";

import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { useRouter } from "next/navigation";
import RightPanel from "./RightPanel";
import DateNav from "./DateNav";
import FilterMenu from "./FilterMenu";
import AudioPlayer from "./AudioPlayer";
import SortToggle from "./SortToggle";
import FeedList from "./FeedList";
import EmptyState from "./ui/EmptyState";
import DesignLegend from "./DesignLegend";
import useMobilePanelBack from "../../lib/useMobilePanelBack";
import useSwipeNav from "../../lib/useSwipeNav";
import useFeedSearch from "../../lib/useFeedSearch";
import useFeedKeyboardNav from "../../lib/useFeedKeyboardNav";

// ── Main feed ─────────────────────────────────────────────────────────────
// Orchestrates the daily feed: holds selection/filter/sort state and the derived
// entry list, then composes DateNav · SortToggle · FilterMenu · FeedList ·
// AudioPlayer over the shared RightPanel. Search lives in useFeedSearch and
// keyboard nav in useFeedKeyboardNav.
//
// projectHints are now server-rendered (initialHints) so the entity/category chips are
// in the first paint — no client round-trip, no waiting for hydration. buildProjectHints
// is 100% DB (zero external calls) and cached per date, so it's cheap to SSR. The
// /api/hints fallback only fires if the page didn't pass hints (defensive).
export default function BulletFeed({
  bullets, analyses = {}, podcasts = [],
  items = [], currentDate, signalsAgo,
  audio = null, initialHints = null,
}) {
  const [selected, setSelected] = useState(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [cursor, setCursor]       = useState(null);
  const [sortBy, setSortBy]       = useState("importance");
  const [active, setActive]       = useState({ news: true, tweets: true, podcasts: true });
  const [pendingOpen, setPendingOpen] = useState(null);
  const [projectHints, setProjectHints] = useState(initialHints ?? []);

  useEffect(() => {
    // Hints came from SSR — nothing to fetch.
    if (initialHints) { setProjectHints(initialHints); return; }
    if (!currentDate) return;
    let cancelled = false;
    fetch(`/api/hints/${currentDate}`)
      .then(r => r.ok ? r.json() : null)
      .then(h => { if (h && !cancelled) setProjectHints(h); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [currentDate, initialHints]);

  const bulletsRef = useRef(null);
  const router = useRouter();

  // ── Mobile swipe to step days (mirrors DateNav's chevron targets) ───────
  const dates = useMemo(() => items.map(it => it.date), [items]);
  const idx = dates.indexOf(currentDate);
  const olderDate = idx >= 0 && idx < dates.length - 1 ? dates[idx + 1] : null; // ‹ back in time
  const newerDate = idx > 0 ? dates[idx - 1] : null;                            // › forward
  const swipe = useSwipeNav(
    useCallback(() => { if (newerDate) router.push(`/d/${newerDate}`); }, [newerDate, router]), // swipe left → newer
    useCallback(() => { if (olderDate) router.push(`/d/${olderDate}`); }, [olderDate, router]), // swipe right → older
  );

  // ── Search (in its own hook; selection-aware via these callbacks) ───────
  const onSearchOpen  = useCallback(() => { setSelected(null); setPanelOpen(true); }, []);
  const onSearchClear = useCallback(() => { if (selected === null) setPanelOpen(false); }, [selected]);
  const { searchQuery, searchProp, handleSearch, handleClearSearch } =
    useFeedSearch({ onSearchOpen, onSearchClear });

  // ── All entries (news + podcasts), pre-filter ──────────────────────────
  const allEntries = useMemo(() => {
    const news = bullets.map((b, i) => ({
      kind:        "news",
      srcType:     b.src?.type === "twitter" ? "tweets" : "news",
      b,
      hint:        projectHints[i] ?? null,
      score:       analyses[b.title]?.importanceScore ?? null,
      sourceCount: analyses[b.title]?.sourceCount ?? null,
      ts:          b.ts,
    }));
    const pods = (podcasts || []).map(p => ({
      kind:    "podcast",
      srcType: "podcasts",
      podcast: p,
      score:   null,
      ts:      p.published_at,
    }));
    return [...news, ...pods];
  }, [bullets, projectHints, analyses, podcasts]);

  const counts = useMemo(() => ({
    news:     allEntries.filter(e => e.srcType === "news").length,
    tweets:   allEntries.filter(e => e.srcType === "tweets").length,
    podcasts: allEntries.filter(e => e.srcType === "podcasts").length,
  }), [allEntries]);

  // ── Filtered + sorted view ─────────────────────────────────────────────
  const entries = useMemo(() => {
    const arr = allEntries.filter(e => active[e.srcType]);
    if (sortBy === "recent") {
      return arr.sort((x, y) => {
        const tx = x.ts ? Date.parse(x.ts) : -Infinity;
        const ty = y.ts ? Date.parse(y.ts) : -Infinity;
        return ty - tx;
      });
    }
    return arr.sort((x, y) => (y.score ?? -Infinity) - (x.score ?? -Infinity));
  }, [allEntries, active, sortBy]);

  const resetSelection = useCallback(() => {
    setSelected(null);
    setCursor(null);
  }, []);

  const handleClose = useCallback(() => {
    setSelected(null);
    setCursor(null);
    if (!searchQuery) setPanelOpen(false);
  }, [searchQuery]);

  const handleSelect = useCallback((i) => {
    setCursor(i);
    if (selected === i) { setSelected(null); setPanelOpen(false); }
    else                { setSelected(i);    setPanelOpen(true);  }
  }, [selected]);

  const handleSortChange = useCallback((next) => {
    setSortBy(next);
    resetSelection();
    if (!searchQuery) setPanelOpen(false);
  }, [searchQuery, resetSelection]);

  const handleToggleSource = useCallback((key) => {
    setActive(a => ({ ...a, [key]: !a[key] }));
    resetSelection();
    if (!searchQuery) setPanelOpen(false);
  }, [searchQuery, resetSelection]);

  const handleAllSources = useCallback(() => {
    setActive({ news: true, tweets: true, podcasts: true });
    resetSelection();
  }, [resetSelection]);

  const handleTagSearch = useCallback((kw) => handleSearch(kw, { entity: true }), [handleSearch]);

  // ── Open story from audio chapter ──────────────────────────────────────
  const handleOpenStory = useCallback((bulletTitle) => {
    if (!bulletTitle) return;
    setActive(a => ({ ...a, news: true }));  // ensure news visible
    setPendingOpen(bulletTitle);
  }, []);

  useEffect(() => {
    if (!pendingOpen) return;
    const i = entries.findIndex(e => e.kind === "news" && e.b?.title === pendingOpen);
    if (i >= 0) {
      handleSelect(i);
      setPendingOpen(null);
    }
  }, [pendingOpen, entries, handleSelect]);

  const closePanel = useCallback(() => {
    setSelected(null);
    setCursor(null);
    setPanelOpen(false);
    if (searchQuery) handleClearSearch();
  }, [searchQuery, handleClearSearch]);
  useMobilePanelBack(panelOpen, closePanel);

  useFeedKeyboardNav({
    count: entries.length, selected, cursor, panelOpen,
    setCursor, setSelected, setPanelOpen, listRef: bulletsRef,
  });

  // ── Derived panel content ──────────────────────────────────────────────
  const selectedEntry   = selected !== null ? (entries[selected] ?? null) : null;
  const isPodcastSel    = selectedEntry?.kind === "podcast";
  const selectedBullet  = isPodcastSel ? null : (selectedEntry?.b ?? null);
  const selectedHint    = isPodcastSel ? null : (selectedEntry?.hint ?? null);
  const selectedAnalysis = selectedBullet ? (analyses[selectedBullet.title]?.analysis ?? null) : null;
  const selectedPodcast = isPodcastSel ? selectedEntry.podcast : null;

  const totalSignals = counts.news + counts.tweets;

  // Audio briefing length variants ready to play (short / standard / explainer), ordered by the API.
  const audioVariants = (audio?.variants || []).filter(v => v.status === "ready" && v.has_audio);

  return (
    <div className="feed-grid">
      <div className="feed-left">
        <div
          className="feed-scroll"
          onClick={handleClose}
          onTouchStart={swipe.onTouchStart}
          onTouchEnd={swipe.onTouchEnd}
        >
          <div className="feed-head" onClick={e => e.stopPropagation()}>
            <div className="feed-head-top">
              <DateNav items={items} currentDate={currentDate} />
              <div className="feed-head-meta">
                <span className="digest-bar-signals">{totalSignals} {totalSignals !== 1 ? "signals" : "signal"}</span>
                {signalsAgo && <><span className="digest-bar-dot">·</span><span className="digest-bar-time">{signalsAgo}</span></>}
              </div>
              <SortToggle value={sortBy} onChange={handleSortChange} />
              <FilterMenu
                active={active}
                counts={counts}
                onToggle={handleToggleSource}
                onAll={handleAllSources}
              />
              <DesignLegend />
            </div>
          </div>

          {entries.length === 0 ? (
            <EmptyState variant="feed" glyph="◷">No items match the current filters.</EmptyState>
          ) : (
            <FeedList
              entries={entries}
              selected={selected}
              cursor={cursor}
              onSelect={handleSelect}
              onTagSearch={handleTagSearch}
              listRef={bulletsRef}
            />
          )}
        </div>

        {audioVariants.length > 0 && (
          <div className="audio-dock">
            <AudioPlayer date={currentDate} variants={audioVariants} onOpenStory={handleOpenStory} />
          </div>
        )}
      </div>

      <div className={`feed-right${panelOpen ? " panel-open" : ""}`}>
        <RightPanel
          bullet={selectedBullet}
          hint={selectedHint}
          cachedAnalysis={selectedAnalysis}
          onClose={handleClose}
          podcast={selectedPodcast}
          search={searchProp}
          date={currentDate}
        />
      </div>
    </div>
  );
}
