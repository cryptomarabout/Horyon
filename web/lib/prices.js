const CG_BASE = "https://api.coingecko.com/api/v3";
const OPTS    = { next: { revalidate: 300 } };

async function safeJson(url) {
  try {
    const r = await fetch(url, OPTS);
    if (!r.ok) return null;
    return r.json();
  } catch { return null; }
}

// Full market snapshot: BTC/ETH prices + 24h/7d/30d changes + dominance + mkt cap
export async function getMarketData() {
  const [coinsData, globalData] = await Promise.all([
    safeJson(
      `${CG_BASE}/coins/markets?vs_currency=usd&ids=bitcoin,ethereum` +
      `&price_change_percentage=7d,30d&sparkline=false&per_page=2&page=1`
    ),
    safeJson(`${CG_BASE}/global`),
  ]);

  const coins  = Array.isArray(coinsData) ? coinsData : [];
  const btcRaw = coins.find(c => c.id === "bitcoin");
  const ethRaw = coins.find(c => c.id === "ethereum");
  const gd     = globalData?.data;

  function extract(raw) {
    if (!raw) return null;
    return {
      price:     raw.current_price     ?? null,
      change24h: raw.price_change_percentage_24h ?? null,
      change7d:  raw.price_change_percentage_7d_in_currency  ?? null,
      change30d: raw.price_change_percentage_30d_in_currency ?? null,
    };
  }

  return {
    btc: extract(btcRaw),
    eth: extract(ethRaw),
    dominance: {
      btc: gd?.market_cap_percentage?.btc ?? null,
      eth: gd?.market_cap_percentage?.eth ?? null,
    },
    totalMarketCap:         gd?.total_market_cap?.usd ?? null,
    totalMarketCapChange24h: gd?.market_cap_change_percentage_24h_usd ?? null,
  };
}
