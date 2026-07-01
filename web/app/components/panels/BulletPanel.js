"use client";

import { useState, useEffect, useRef } from "react";
import { fmtTvl, fmtShortDate } from "../../../lib/format";
import { XIcon, ExtIcon } from "../icons";
import PanelSection from "../ui/PanelSection";
import PanelHeader from "../ui/PanelHeader";
import PanelBody from "../ui/PanelBody";
import SkeletonLines from "../ui/SkeletonLines";

// ── Price formatter — "$1.2K" compact style (panel-specific) ───────────────
function fmtPrice(usd) {
  if (usd == null) return null;
  if (usd >= 10000) return `$${(usd / 1000).toFixed(1)}K`;
  if (usd >= 1)     return `$${usd.toFixed(2)}`;
  if (usd >= 0.01)  return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(5)}`;
}

// Hide a logo/image that fails to load rather than showing a broken-image glyph.
const hideOnError = (e) => { e.currentTarget.style.visibility = "hidden"; };

// ── Chain distribution from chain_tvls JSONB ───────────────────────────────
function computeChainDist(chainTvls) {
  if (!chainTvls || typeof chainTvls !== "object") return [];
  const entries = Object.entries(chainTvls)
    .filter(([name]) => !name.includes("-") && !name.toLowerCase().includes("borrowed"))
    .map(([name, val]) => {
      const tvl = typeof val === "number" ? val
        : typeof val?.tvl === "number" ? val.tvl
        : 0;
      return { name, tvl };
    })
    .filter(e => e.tvl > 0)
    .sort((a, b) => b.tvl - a.tvl)
    .slice(0, 6);
  const total = entries.reduce((s, e) => s + e.tvl, 0);
  return entries.map(e => ({ ...e, pct: total > 0 ? (e.tvl / total) * 100 : 0 }));
}

const chainLogoUrl = name =>
  `https://icons.llamao.fi/icons/chains/rsz_${encodeURIComponent(name.toLowerCase())}.jpg`;

// ── Protocol card ──────────────────────────────────────────────────────────
function ProtocolCard({ p }) {
  const chg      = p.tvl_change_1d;
  const chgCls   = chg == null ? "" : chg > 0 ? "up" : chg < 0 ? "dn" : "";
  const chainDist = computeChainDist(p.chain_tvls);
  return (
    <div className="panel-proto-card">
      <div className="panel-proto-top">
        {p.logo_url ? (
          <img src={p.logo_url} alt={p.name} className="panel-proto-logo"
            onError={hideOnError} />
        ) : (
          <div className="panel-proto-logo" />
        )}
        <div className="panel-proto-info">
          <div className="panel-proto-name">{p.name}</div>
          {p.category && <div className="panel-proto-cat">{p.category}</div>}
          {fmtPrice(p.price) && (
            <div className="panel-proto-cat"
              style={{ color: "var(--accent-bright)", fontFamily: "var(--mono)", fontSize: "10px", marginTop: "2px" }}>
              {fmtPrice(p.price)}
            </div>
          )}
        </div>
        <div className="panel-proto-meta">
          {fmtTvl(p.tvl_usd) && <span className="panel-proto-tvl">{fmtTvl(p.tvl_usd)}</span>}
          {chg != null && (
            <span className={`panel-proto-chg ${chgCls}`}>
              {chg > 0 ? "▲" : chg < 0 ? "▼" : "–"}{Math.abs(chg).toFixed(1)}%
            </span>
          )}
          {p.url && (
            <a href={p.url} target="_blank" rel="noreferrer"
              className="panel-proto-link" onClick={e => e.stopPropagation()}>
              defillama.com ↗
            </a>
          )}
        </div>
      </div>
      {chainDist.length > 0 && (
        <div className="chain-dist-list">
          {chainDist.map(c => (
            <div key={c.name} className="chain-dist-row">
              <div className="chain-dist-info">
                <img src={chainLogoUrl(c.name)} alt={c.name} className="chain-dist-logo"
                  onError={hideOnError} />
                <span className="chain-dist-name">{c.name}</span>
                <span className="chain-dist-tvl">{fmtTvl(c.tvl)}</span>
                <span className="chain-dist-pct">{c.pct.toFixed(1)}%</span>
              </div>
              <div className="chain-dist-bar">
                <div className="chain-dist-fill" style={{ width: `${c.pct}%` }} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Chain card ─────────────────────────────────────────────────────────────
function ChainCard({ chain }) {
  return (
    <div className="panel-chain-card">
      <img src={chainLogoUrl(chain.name)} alt={chain.name} className="panel-chain-logo"
        onError={hideOnError} />
      <div className="panel-chain-info">
        <div className="panel-chain-name">{chain.name}</div>
        {chain.rank && <div className="panel-chain-rank">Rank #{chain.rank}</div>}
      </div>
      {chain.tvl != null && fmtTvl(chain.tvl) && (
        <span className="panel-chain-tvl">{fmtTvl(chain.tvl)}</span>
      )}
    </div>
  );
}

// ── Source row ─────────────────────────────────────────────────────────────
function SourceRow({ source }) {
  const isTwitter = source.type === "twitter";
  return (
    <a href={source.link} target="_blank" rel="noreferrer"
      className="panel-source-row" onClick={e => e.stopPropagation()}>
      {isTwitter ? <XIcon size={9} /> : <ExtIcon size={9} />}
      <span className="panel-source-name">{source.name}</span>
      {source.isPrimary && <span className="panel-source-primary">cited</span>}
    </a>
  );
}

// ── Related article row ─────────────────────────────────────────────────────
function RelatedArticle({ article, onOpen }) {
  return (
    <div className="related-item related-item--clickable"
      onClick={() => onOpen(article)} role="button" tabIndex={0}
      onKeyDown={e => e.key === "Enter" && onOpen(article)}>
      <div className="related-item-head">
        <span className="related-date">{fmtShortDate(article.date)}</span>
        <span className="related-title">{article.title}</span>
      </div>
      {article.body && <p className="related-body">{article.body}</p>}
    </div>
  );
}

// ── Main BulletPanel — owns AI/sources/related fetch state ─────────────────
export default function BulletPanel({ bullet, hint, cachedAnalysis, onClose, date, onOpenArticle }) {
  const [aiState, setAiState] = useState("idle");
  const [aiText,  setAiText]  = useState("");
  const [related,        setRelated]        = useState([]);
  const [relatedLoading, setRelatedLoading] = useState(false);
  const [sources,        setSources]        = useState([]);
  const [sourcesLoading, setSourcesLoading] = useState(false);
  const prevTitle = useRef(null);

  // AI analysis
  useEffect(() => {
    if (!bullet) { setAiState("idle"); setAiText(""); return; }
    if (bullet.title === prevTitle.current) return;
    prevTitle.current = bullet.title;

    if (cachedAnalysis) {
      setAiText(cachedAnalysis);
      setAiState("done");
      return;
    }

    setAiState("loading");
    setAiText("");

    const ctrl = new AbortController();
    fetch("/api/details", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: bullet.title, body: bullet.body }),
      signal: ctrl.signal,
    })
      .then(r => r.json())
      .then(data => { setAiText(data.content || "No additional details."); setAiState("done"); })
      .catch(err => {
        if (err.name === "AbortError") return;
        setAiText("Could not load analysis.");
        setAiState("error");
      });
    return () => ctrl.abort();
  }, [bullet?.title, cachedAnalysis]);

  // Related articles
  useEffect(() => {
    if (!bullet) { setRelated([]); setRelatedLoading(false); return; }
    const protocols = (hint?.protocols || []).map(p => p.name);
    const chains    = (hint?.chains    || []).map(c => c.name);
    setRelated([]); setRelatedLoading(true);
    const ctrl = new AbortController();
    fetch("/api/related", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ protocols, chains, title: bullet.title }),
      signal: ctrl.signal,
    })
      .then(r => r.json())
      .then(data => { setRelated(data.articles || []); setRelatedLoading(false); })
      .catch(err => { if (err.name !== "AbortError") setRelatedLoading(false); });
    return () => ctrl.abort();
  }, [bullet?.title, hint]);

  // All corroborating sources
  useEffect(() => {
    if (!bullet || !date) { setSources([]); setSourcesLoading(false); return; }
    const protocols = (hint?.protocols   || []).map(p => p.name);
    const chains    = (hint?.chains      || []).map(c => c.name);
    const entities  = (hint?.entityTags  || []).map(e => e.name);
    setSources([]); setSourcesLoading(true);
    const ctrl = new AbortController();
    fetch("/api/sources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: bullet.title, body: bullet.body, date,
        protocols, chains, entities, primaryLink: bullet.link || null,
      }),
      signal: ctrl.signal,
    })
      .then(r => r.json())
      .then(data => { setSources(data.sources || []); setSourcesLoading(false); })
      .catch(err => { if (err.name !== "AbortError") setSourcesLoading(false); });
    return () => ctrl.abort();
  }, [bullet?.title, hint, date]);

  const { protocols = [], chains = [] } = hint || {};
  const hasProjects = protocols.length > 0 || chains.length > 0;
  const srcIsTwitter = bullet?.src?.type === "twitter";

  return (
    <>
      <PanelHeader
        onClose={onClose}
        below={bullet.link && (
          <a href={bullet.link} target="_blank" rel="noreferrer"
            className="panel-src-link" onClick={e => e.stopPropagation()}>
            {srcIsTwitter ? <XIcon size={9} /> : null}
            <span>{bullet.src?.name ?? "Source"}</span>
            <ExtIcon size={9} />
          </a>
        )}
      >
        <h2 className="panel-title">{bullet.title}</h2>
      </PanelHeader>

      <PanelBody>
        <PanelSection label="Analyst View">
          {aiState === "loading" && <SkeletonLines widths={[100, 92, 84, 55]} />}
          {aiState !== "loading" && (
            <p className={`panel-ai-text${aiState === "error" ? " error" : ""}`}>
              {aiText}
            </p>
          )}
        </PanelSection>

        {(sourcesLoading || sources.length > 0) && (
          <PanelSection label="Sources" count={sources.length > 0 ? sources.length : null}>
            {sourcesLoading && sources.length === 0 ? (
              <SkeletonLines widths={[70, 60]} prefix="related" />
            ) : (
              <div className="panel-source-list">
                {sources.map((s, i) => <SourceRow key={i} source={s} />)}
              </div>
            )}
          </PanelSection>
        )}

        {protocols.length > 0 && (
          <PanelSection label="DeFiLlama · Protocol TVL">
            {protocols.map(p => <ProtocolCard key={p.name} p={p} />)}
          </PanelSection>
        )}

        {chains.length > 0 && (
          <PanelSection label="Chain TVL">
            {chains.map(c => <ChainCard key={c.name} chain={c} />)}
          </PanelSection>
        )}

        {!hasProjects && aiState !== "loading" && (
          <p style={{ fontSize: "11px", color: "var(--text-4)", lineHeight: 1.5 }}>
            No entity found on DeFiLlama for this news.
          </p>
        )}

        {(relatedLoading || related.length > 0) && (
          <PanelSection label="Related Stories">
            {relatedLoading && related.length === 0 && (
              <SkeletonLines widths={[85, 72, 90]} prefix="related" />
            )}
            <div className="related-list">
              {related.map((a, i) => (
                <RelatedArticle key={i} article={a} onOpen={onOpenArticle} />
              ))}
            </div>
          </PanelSection>
        )}
      </PanelBody>
    </>
  );
}
