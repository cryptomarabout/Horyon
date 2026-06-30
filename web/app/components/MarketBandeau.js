"use client";

import { useState } from "react";
import { fmtTvl } from "../../lib/format";

// ── Price formatter — "1,234 $" market-bar style (differs from panel's "$1.2K") ──
// Prices ≥ $1,000 (BTC, ETH) render without decimals.
function fmtPrice(usd) {
  if (usd == null) return null;
  const decimals = usd >= 1000 ? 0 : 1;
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(usd) + " $";
}

// Compact (mobile) price — BTC collapses thousands to "k" (104,231 → "104k $");
// everything else falls back to the full formatter.
function fmtPriceCompact(sym, usd) {
  if (usd == null) return null;
  if (sym === "BTC" && usd >= 1000) return `${Math.round(usd / 1000)}k $`;
  return fmtPrice(usd);
}

function fmtPct(pct, compact = false) {
  if (pct == null) return null;
  const abs = Math.abs(pct).toFixed(compact ? 1 : 2);
  return { tri: pct > 0 ? "▲" : pct < 0 ? "▼" : "–", val: `${abs}%`, cls: pct > 0 ? "up" : pct < 0 ? "dn" : "flat" };
}

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
  if (failed || !logoUrl) return <span className={`market-glyph ${cls}`}>{glyph}</span>;
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
            <span className="market-price market-price--full">{fmtPrice(d.price)}</span>
            <span className="market-price market-price--compact">{fmtPriceCompact(sym, d.price)}</span>
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

function TvlStrip({ tvl }) {
  // Only the aggregate "All DeFi" figure — per-chain rows were dropped.
  const total = tvl?.find(row => row.chain === "total");
  if (!total) return null;
  const pct = total.pct;
  const up = pct !== null && pct > 0;
  const down = pct !== null && pct < 0;
  return (
    <div className="tvl-strip">
      <div className="tvl-strip-items">
        <div className="tvl-chip tvl-chip--total">
          <span className="tvl-chip-chain">All DeFi</span>
          <span className="tvl-chip-val">{fmtTvl(total.tvl_now)}</span>
          {pct !== null && (
            <span className={`tvl-chip-chg ${up ? "up" : down ? "down" : "flat"}`}>
              <span className="tvl-tri" aria-hidden>{up ? "▲" : down ? "▼" : "–"}</span>
              {Math.abs(pct).toFixed(2)}%
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Bandeau wrapper — rendered at the bottom of the feed column ─────────────
export default function MarketBandeau({ market, tvl }) {
  if (!market && !tvl?.length) return null;
  return (
    <div className="market-bandeau">
      {market && <MarketBar market={market} />}
      {tvl?.length > 0 && <TvlStrip tvl={tvl} />}
    </div>
  );
}
