// Parse a stored digest (Telegram-HTML bullets) into structured items.
// Each line looks like:  • <b>Title</b> — Body sentence(s). <a href="url">🔗</a>

// True only for a real calendar date in YYYY-MM-DD form. The format regex alone is NOT
// enough: a syntactically valid but out-of-range date like 2026-13-99 or 2026-02-30 slips
// past it and then throws on the SQL date cast → a 500 (or a naked error page on /d/<date>).
// Round-tripping through Date catches the overflow.
export function isValidDigestDate(s) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s ?? "")) return false;
  const d = new Date(`${s}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
}

export function timeAgo(isoStr) {
  if (!isoStr) return null;
  try {
    const diffMs = Date.now() - new Date(isoStr + "Z").getTime();
    if (diffMs < 0) return "just now";
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1)  return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const hrs = Math.floor(diffMin / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  } catch { return null; }
}

export const DOMAIN_NAMES = {
  "cointelegraph.com": "CoinTelegraph", "coindesk.com": "CoinDesk",
  "theblock.co": "The Block", "decrypt.co": "Decrypt",
  "blockworks.co": "Blockworks", "beincrypto.com": "BeInCrypto",
  "cryptoslate.com": "CryptoSlate", "bitcoinmagazine.com": "Bitcoin Magazine",
  "dlnews.com": "DL News", "thedefiant.io": "The Defiant",
  "bankless.com": "Bankless", "unchainedcrypto.com": "Unchained",
  "protos.com": "Protos", "wired.com": "Wired",
  "reuters.com": "Reuters", "bloomberg.com": "Bloomberg",
};

export function sourceLabel(url) {
  if (!url) return null;
  try {
    const u    = new URL(url);
    const host = u.hostname.replace(/^www\./, "");
    if (host === "x.com" || host === "twitter.com" || host.includes("nitter.")) {
      const user = u.pathname.split("/").filter(Boolean)[0];
      if (user && !["i","search","explore","home"].includes(user))
        return { type: "twitter", name: "@" + user };
      return { type: "twitter", name: "X" };
    }
    if (DOMAIN_NAMES[host]) return { type: "news", name: DOMAIN_NAMES[host] };
    const base = host.split(".")[0];
    return { type: "news", name: base.charAt(0).toUpperCase() + base.slice(1) };
  } catch { return null; }
}

const ENTITIES = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#39;": "'",
  "&apos;": "'",
  "&nbsp;": " ",
};

function decode(s) {
  return s.replace(/&[a-z#0-9]+;/gi, (m) => ENTITIES[m] ?? m);
}

function stripTags(s) {
  return s.replace(/<[^>]+>/g, "");
}

export function parseDigest(content) {
  const lines = (content || "").replace(/\r/g, "").split("\n");
  const bullets = [];
  let heading = "";

  for (const line of lines) {
    const t = line.trim();
    if (!t) continue;
    if (t.startsWith("#")) {
      heading = t.replace(/^#+\s*/, "").trim();
      continue;
    }
    if (!t.startsWith("•")) continue;

    const body0 = t.replace(/^•\s*/, "");
    const titleMatch = body0.match(/<b>([\s\S]*?)<\/b>/i);
    const linkMatch = body0.match(/<a[^>]*href="([^"]+)"/i);

    const rest = body0
      .replace(/<b>[\s\S]*?<\/b>/i, "")
      .replace(/<a[^>]*>[\s\S]*?<\/a>/gi, "")
      .replace(/^\s*[—–-]+\s*/, "")
      .trim();

    const title = decode(stripTags(titleMatch?.[1] ?? "")).trim();
    const body = decode(stripTags(rest)).replace(/\s+/g, " ").trim();

    bullets.push({
      title,
      body,
      link: linkMatch?.[1],
      hack: /🚨/.test(t),
    });
  }

  return { heading, bullets };
}
