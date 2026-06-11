"use client";

import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import BulletItem from "./BulletItem";
import PodcastFeedItem from "./PodcastFeedItem";
import RightPanel from "./RightPanel";
import DateNav from "./DateNav";
import SourceFilter from "./SourceFilter";

// ── Sort toggle — beside the digest date ────────────────────────────────────
const SORT_OPTS = [
  { key: "importance", label: "Importance" },
  { key: "recent",     label: "Recent" },
];

function SortToggle({ value, onChange }) {
  return (
    <div className="sort-toggle" role="group" aria-label="Sort signals" onClick={e => e.stopPropagation()}>
      {SORT_OPTS.map(o => (
        <button
          key={o.key}
          type="button"
          className={`sort-toggle-btn${value === o.key ? " is-active" : ""}`}
          aria-pressed={value === o.key}
          onClick={() => onChange(o.key)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ── Formatters ────────────────────────────────────────────────────────────
function fmtPrice(usd) {
  if (usd == null) return null;
  const decimals = usd >= 10000 ? 0 : 1;
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(usd) + " $";
}

function fmtTvl(usd) {
  if (usd >= 1e12) return `$${(usd / 1e12).toFixed(2)}T`;
  if (usd >= 1e9)  return `$${(usd / 1e9).toFixed(1)}B`;
  if (usd >= 1e6)  return `$${(usd / 1e6).toFixed(0)}M`;
  return `$${usd.toLocaleString()}`;
}

function fmtPct(pct, compact = false) {
  if (pct == null) return null;
  const abs = Math.abs(pct).toFixed(compact ? 1 : 2);
  return { tri: pct > 0 ? "▲" : pct < 0 ? "▼" : "–", val: `${abs}%`, cls: pct > 0 ? "up" : pct < 0 ? "dn" : "flat" };
}

const CHAIN_LABEL = {
  total: "All DeFi", Ethereum: "ETH", BSC: "BSC",
  Solana: "SOL", Tron: "Tron", Base: "Base", Arbitrum: "ARB",
};

// ── Market data bar ───────────────────────────────────────────────────────
const MARKET_ASSETS = [
  { sym: "BTC", key: "btc", domKey: "btc", cls: "rot-btc", glyph: "₿",
    logoUrl: "https://assets.coingecko.com/coins/images/1/small/bitcoin.png" },
  { sym: "ETH", key: "eth", domKey: "eth", cls: "rot-eth", glyph: "Ξ",
    logoUrl: "https://assets.coingecko.com/coins/images/279/small/ethereum.png" },
];
const CHANGES = [
  { key: "change24h", label: "24h", period: "p24h" },
  { key: "change7d",  label: "7d",  period: "p7d"  },
  { key: "change30d", label: "30d", period: "p30d" },
];

function AssetLogo({ sym, cls, glyph, logoUrl }) {
  const [failed, setFailed] = useState(false);
  if (failed || !logoUrl) {
    return <span className={`market-glyph ${cls}`}>{glyph}</span>;
  }
  return <img src={logoUrl} alt={sym} className="market-asset-logo" onError={() => setFailed(true)} />;
}

function MarketBar({ market }) {
  if (!market) return null;
  const assets = MARKET_ASSETS.filter(a => market[a.key] != null);
  if (!assets.length) return null;

  const hasFooter = market.totalMarketCap != null ||
    MARKET_ASSETS.some(a => market.dominance?.[a.domKey] != null);

  return (
    <div className="market-bar">
      {assets.map(({ sym, key, domKey, cls, glyph, logoUrl }, idx) => {
        const d = market[key];
        return (
          <div key={sym} className="market-asset-inline">
            {idx > 0 && <span className="market-divider" />}
            <AssetLogo sym={sym} cls={cls} glyph={glyph} logoUrl={logoUrl} />
            <span className="market-price">{fmtPrice(d.price)}</span>
            <span className="market-chg-group">
              {CHANGES.map(({ key: ck, label, period }) => {
                const p = fmtPct(d[ck], true);
                if (!p) return null;
                return (
                  <span key={ck} className={`market-chg market-chg--${p.cls} ${period}`}>
                    <span className="market-chg-label">{label}</span>
                    <span className="market-chg-tri">{p.tri}</span>
                    <span className="market-chg-pct">{p.val}</span>
                  </span>
                );
              })}
            </span>
          </div>
        );
      })}

      {hasFooter && (
        <div className="market-footer-inline">
          {market.totalMarketCap != null && (
            <span className="market-footer-item">
              <span className="market-footer-label">MCap</span>
              <span className="market-footer-val">{fmtTvl(market.totalMarketCap)}</span>
            </span>
          )}
          {MARKET_ASSETS.map(({ sym, domKey, cls }) => {
            const dom = market.dominance?.[domKey];
            if (dom == null) return null;
            return (
              <span key={sym} className="market-footer-item">
                <span className={`market-footer-label ${cls}`}>{sym}</span>
                <span className="market-footer-val">{dom.toFixed(1)}%</span>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── DeFi TVL strip — includes market MCap + dominance as first chips ─────
function TvlStrip({ tvl, market }) {
  if (!tvl?.length) return null;
  return (
    <div className="tvl-strip">
      <div className="tvl-strip-items">
        {market?.totalMarketCap != null && (() => {
          const chg = fmtPct(market.totalMarketCapChange24h, true);
          return (
            <div className="tvl-chip tvl-chip--mkt">
              <span className="tvl-chip-chain">MCap</span>
              <span className="tvl-chip-val">{fmtTvl(market.totalMarketCap)}</span>
              {chg && <span className={`tvl-chip-chg ${chg.cls}`}><span className="tvl-tri">{chg.tri}</span>{chg.val}</span>}
            </div>
          );
        })()}
        {market?.dominance?.btc != null && (() => {
          const chg = fmtPct(market.btc?.change24h, true);
          return (
            <div className="tvl-chip tvl-chip--mkt">
              <span className="tvl-chip-chain rot-btc">BTC</span>
              <span className="tvl-chip-val">{market.dominance.btc.toFixed(1)}%</span>
              {chg && <span className={`tvl-chip-chg ${chg.cls}`}><span className="tvl-tri">{chg.tri}</span>{chg.val}</span>}
            </div>
          );
        })()}
        {market?.dominance?.eth != null && (() => {
          const chg = fmtPct(market.eth?.change24h, true);
          return (
            <div className="tvl-chip tvl-chip--mkt">
              <span className="tvl-chip-chain rot-eth">ETH</span>
              <span className="tvl-chip-val">{market.dominance.eth.toFixed(1)}%</span>
              {chg && <span className={`tvl-chip-chg ${chg.cls}`}><span className="tvl-tri">{chg.tri}</span>{chg.val}</span>}
            </div>
          );
        })()}
        {tvl.map(row => {
          const pct = row.pct;
          const up = pct !== null && pct > 0;
          const down = pct !== null && pct < 0;
          return (
            <div key={row.chain} className={`tvl-chip${row.chain === "total" ? " tvl-chip--total" : ""}`}>
              <span className="tvl-chip-chain">{CHAIN_LABEL[row.chain] ?? row.chain}</span>
              <span className="tvl-chip-val">{fmtTvl(row.tvl_now)}</span>
              {pct !== null && (
                <span className={`tvl-chip-chg ${up ? "up" : down ? "down" : "flat"}`}>
                  <span className="tvl-tri" aria-hidden>{up ? "▲" : down ? "▼" : "–"}</span>
                  {Math.abs(pct).toFixed(2)}%
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Main feed ─────────────────────────────────────────────────────────────
export default function BulletFeed({
  bullets, projectHints, analyses = {}, podcasts = [],
  items = [], currentDate, signalsAgo,
  tvl, market,
}) {
  const [selected, setSelected] = useState(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [cursor, setCursor]       = useState(null);
  const [sortBy, setSortBy]       = useState("importance");
  const [active, setActive]       = useState({ news: true, tweets: true, podcasts: true });

  const bulletsRef = useRef(null);

  // Search state (driven by the header NavSearch via the event bus)
  const [searchQuery,  setSearchQuery]  = useState("");
  const [searchState,  setSearchState]  = useState("idle");
  const [searchResult, setSearchResult] = useState("");
  const [searchSrcs,   setSearchSrcs]   = useState(0);

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

  // Selection is position-based, so reset it whenever the order/filtering changes.
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

  // ── Search ─────────────────────────────────────────────────────────────
  const handleSearch = useCallback(async (kw) => {
    setSearchQuery(kw);
    setSearchState("loading");
    setSearchResult("");
    setSearchSrcs(0);
    setSelected(null);
    setPanelOpen(true);
    document.dispatchEvent(new CustomEvent("horyon:search-loading"));
    try {
      const r = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keyword: kw }),
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
  }, []);

  const handleClearSearch = useCallback(() => {
    setSearchQuery("");
    setSearchState("idle");
    setSearchResult("");
    setSearchSrcs(0);
    document.dispatchEvent(new CustomEvent("horyon:clear-input"));
    if (selected === null) setPanelOpen(false);
  }, [selected]);

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

  // ── Keyboard nav ───────────────────────────────────────────────────────
  const kbRef = useRef({ selected: null, cursor: null, panelOpen: false, count: 0 });
  useEffect(() => {
    kbRef.current = { selected, cursor, panelOpen, count: entries.length };
  });
  useEffect(() => {
    function onKeyDown(e) {
      const inInput = e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.isContentEditable;
      if (e.key === "/") {
        if (inInput) return;
        e.preventDefault();
        document.dispatchEvent(new CustomEvent("horyon:focus-search"));
        return;
      }
      if (inInput) return;
      if (e.target.closest?.(".bullet")) return;

      const { selected: sel, cursor: cur, count } = kbRef.current;
      if (!count) return;

      switch (e.key) {
        case "ArrowDown":
        case "j":
        case "J":
          e.preventDefault();
          setCursor(c => c === null ? 0 : Math.min(c + 1, count - 1));
          break;
        case "ArrowUp":
        case "k":
        case "K":
          e.preventDefault();
          setCursor(c => c === null ? count - 1 : Math.max(c - 1, 0));
          break;
        case "ArrowRight":
        case "Enter":
          e.preventDefault();
          if (cur !== null) { setSelected(cur); setPanelOpen(true); }
          break;
        case "ArrowLeft":
        case "Escape":
          e.preventDefault();
          if (sel !== null) { setCursor(sel); setSelected(null); setPanelOpen(false); }
          else if (cur !== null) { setCursor(null); }
          break;
        default:
          break;
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (cursor === null) return;
    const ul = bulletsRef.current;
    if (!ul) return;
    ul.children[cursor]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [cursor]);

  // ── Derived panel content ──────────────────────────────────────────────
  const selectedEntry    = selected !== null ? (entries[selected] ?? null) : null;
  const isPodcastSel      = selectedEntry?.kind === "podcast";
  const selectedBullet    = isPodcastSel ? null : (selectedEntry?.b ?? null);
  const selectedHint      = isPodcastSel ? null : (selectedEntry?.hint ?? null);
  const selectedAnalysis  = selectedBullet ? (analyses[selectedBullet.title]?.analysis ?? null) : null;
  const selectedPodcast   = isPodcastSel ? selectedEntry.podcast : null;

  const searchProp = searchQuery
    ? { keyword: searchQuery, state: searchState, result: searchResult, sources: searchSrcs, onClose: handleClearSearch }
    : null;

  const totalSignals = counts.news + counts.tweets;

  return (
    <div className="feed-grid">
      <div className="feed-left">
        <div className="feed-scroll" onClick={handleClose}>
          {/* ── Masthead: date stepper + meta + sort ── */}
          <div className="feed-head" onClick={e => e.stopPropagation()}>
            <div className="feed-head-top">
              <DateNav items={items} currentDate={currentDate} />
              <div className="feed-head-meta">
                <span className="digest-bar-signals">{totalSignals} {totalSignals !== 1 ? "signals" : "signal"}</span>
                {signalsAgo && <><span className="digest-bar-dot">·</span><span className="digest-bar-time">{signalsAgo}</span></>}
              </div>
              <SortToggle value={sortBy} onChange={handleSortChange} />
            </div>
            <div className="feed-head-filters">
              <SourceFilter
                active={active}
                counts={counts}
                onToggle={handleToggleSource}
                onAll={handleAllSources}
              />
            </div>
          </div>

          {/* ── Feed ── */}
          {entries.length === 0 ? (
            <div className="feed-empty">
              <div className="feed-empty-glyph" aria-hidden>◷</div>
              <p>No items match the current filters.</p>
            </div>
          ) : (
            <ul className="bullets" ref={bulletsRef}>
              {entries.map((e, i) => (
                e.kind === "podcast" ? (
                  <PodcastFeedItem
                    key={`pod:${e.podcast.video_id}`}
                    podcast={e.podcast}
                    selected={selected === i}
                    cursor={cursor === i && selected !== i}
                    onSelect={() => handleSelect(i)}
                  />
                ) : (
                  <BulletItem
                    key={`news:${i}:${e.b.title}`}
                    title={e.b.title}
                    body={e.b.body}
                    hack={e.b.hack}
                    src={e.b.src}
                    link={e.b.link}
                    ts={e.b.ts}
                    projectHint={e.hint}
                    importanceScore={e.score}
                    sourceCount={e.sourceCount}
                    selected={selected === i}
                    cursor={cursor === i && selected !== i}
                    onSelect={() => handleSelect(i)}
                    onTagSearch={handleSearch}
                  />
                )
              ))}
            </ul>
          )}
        </div>

        {/* Market data + DeFi TVL bandeau */}
        {(market || tvl?.length > 0) && (
          <div className="market-bandeau">
            {market && <MarketBar market={market} />}
            {tvl?.length > 0 && <TvlStrip tvl={tvl} market={market} />}
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
        />
      </div>
    </div>
  );
}
