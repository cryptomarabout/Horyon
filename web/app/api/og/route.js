import { ImageResponse } from "next/og";
import { readFile } from "fs/promises";
import path from "path";
import { getDigest, getBulletAnalyses, latestDate, getOgCard } from "../../../lib/db";
import { isValidDigestDate } from "../../../lib/digest";

export const dynamic = "force-dynamic";

// Edge/browser cache for the card. The bot re-warms digest_og post-digest, so a 10-min TTL
// is a fine ceiling on how long a regenerated card takes to propagate.
const OG_CACHE_CONTROL = "public, max-age=600, s-maxage=600, stale-while-revalidate=86400";

// ── Canvas ───────────────────────────────────────────────────────────────────
// Twitter summary_large_image renders 2:1; 1200×628 is the safe landscape size
// (the old 1080×1080 square was center-cropped in the timeline).
const W = 1200;
const H = 628;
const PAD = 56; // horizontal content margin

// ── Fixed section heights (signal list takes whatever remains) ────────────────
const HEADER_H = 70; // brand + date bar
const GOLD_BAR = 3; // gold rule under header
const HERO_H = 190; // top-signal hero block (headline + multi-line factual detail)
const DIVIDER = 1; // hairline before the signal list
const FOOTER_RULE = 1; // gold rule above footer
const FOOTER_H = 52; // CTA bar
const SIGNAL_H = H - HEADER_H - GOLD_BAR - HERO_H - DIVIDER - FOOTER_RULE - FOOTER_H;
// = 628 - 70 - 3 - 190 - 1 - 1 - 52 = 311

// ── Brand palette ────────────────────────────────────────────────────────────
const BG = "#060606";
const TEXT = "#F0EDE6";
const TEXT2 = "#A7AFBC";
const TEXT3 = "#5E6B80";
const TEXT4 = "#3C4659";
const ACCENT = "#D4AF37";
const GOLD_CTA = "#CDA94A"; // muted gold — footer CTA

// ── Module-level caches ──────────────────────────────────────────────────────
let _fontsP = null;
let _assetsP = null;

// ── Font loading ─────────────────────────────────────────────────────────────
// Google Fonts v1 API without a User-Agent header returns TTF (truetype).
// @vercel/og in Next.js 14.x only accepts TTF/OTF — woff/woff2 throw
// "Unsupported OpenType signature".  Do NOT set a browser UA.
async function loadGoogleFont(family, weight) {
  const qs = new URLSearchParams({ family: `${family}:${weight}` });
  const css = await fetch(`https://fonts.googleapis.com/css?${qs}`).then((r) => r.text());
  const m = css.match(/src:\s*url\(([^)]+)\)\s*format\(['"]?truetype['"]?\)/);
  if (!m) throw new Error(`No truetype URL for ${family} ${weight}`);
  const data = await fetch(m[1]).then((r) => r.arrayBuffer());
  return { name: family, weight, style: "normal", data };
}

function getFonts() {
  if (!_fontsP) {
    _fontsP = Promise.all([
      loadGoogleFont("Raleway", 800),
      loadGoogleFont("Raleway", 700),
      loadGoogleFont("Raleway", 500),
      loadGoogleFont("DM Mono", 400),
    ]).catch((err) => {
      _fontsP = null;
      throw err;
    });
  }
  return _fontsP;
}

// ── Asset loading ────────────────────────────────────────────────────────────
function getAssets() {
  if (!_assetsP) {
    _assetsP = readFile(path.join(process.cwd(), "public/falcon.png"))
      .then((buf) => ({ falcon: `data:image/png;base64,${buf.toString("base64")}` }))
      .catch((err) => {
        _assetsP = null;
        throw err;
      });
  }
  return _assetsP;
}

// ── Category detection ────────────────────────────────────────────────────────
const CATS = [
  [/hack|exploit|drain|draining|attack|vuln|rug|scam|breach|freeze|blacklist/i, "SECURITY", "#EF4444"],
  [/govern|vote|dao|proposal|futarch|on-chain|onchain/i, "GOVERNANCE", "#A855F7"],
  [/privacy|encrypt|zkp|private|confidential|zero.?knowledge/i, "PRIVACY", "#8B5CF6"],
  [/layer.?[12]|\bl[12]\b|rollup|zk.?proof|bridge|infra|devnet|testnet|mainnet|hard.?fork|upgrade|scaling|consensus|sequencer|validator|quantum|post.?quantum|evm|signature/i, "INFRA", "#6366F1"],
  [/defi|tvl|liquidity|pool|yield|amm|dex|lend|borrow|vault|protocol|collateral|apy|stablecoin/i, "DEFI", "#10B981"],
  [/\bai\b|llm|agent|model|machine.?learn|gpt|intelligence|fetch.*skill|skills.*launch/i, "AI", "#F59E0B"],
  [/browser|wallet|ux|app|platform|sdk|cli|launch|deploy|tool|comet|card/i, "PRODUCT", "#60A5FA"],
  [/btc|bitcoin|halv|miner|hash/i, "BITCOIN", "#F97316"],
  [/regul|(?<!\w)sec(?!\w)|cftc|legal|law|court|comply|compliance/i, "REGULATORY", "#EC4899"],
  [/market|price|bull|bear|rally|dump|ath|fund|vc|raise|capital|valuat|whale|exits?\b|position/i, "MARKETS", ACCENT],
];

function detectCat(title, body) {
  const t = title + " " + body;
  for (const [re, label, color] of CATS) {
    if (re.test(t)) return { label, color };
  }
  return { label: "SIGNAL", color: ACCENT };
}

// ── Helpers ───────────────────────────────────────────────────────────────────
const DAYS_SHORT = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

function fmtDate(dateStr) {
  const d = new Date(dateStr + "T00:00:00Z");
  return `${DAYS_SHORT[d.getUTCDay()]} · ${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
}

// Clean word-boundary clip — NO trailing ellipsis (a visible "…" reads as cut-off
// on a social card).  Strips dangling punctuation left by the cut.
const TRAIL_STOP = /[\s,;:.—–-]*\b(?:with|its|the|a|an|of|on|to|and|for|in|at|by|as|or|from|into|over|that|this|s)\s*$/i;
function clip(str, max) {
  const s = (str || "").replace(/\s+/g, " ").trim();
  if (s.length <= max) return s;
  const cut = s.slice(0, max);
  const sp = cut.lastIndexOf(" ");
  let out = (sp > max * 0.6 ? cut.slice(0, sp) : cut).replace(/[\s,;:.—–-]+$/, "").trim();
  // Drop a dangling trailing connective word so the clip doesn't read mid-thought.
  out = out.replace(TRAIL_STOP, "").trim();
  return out;
}

// Sentence-aware fit for the detail lines: keep as many WHOLE sentences as fit in `max`
// so a detail never trails off mid-thought ("...diversified earn").  Falls back to a
// word-boundary clip only when the very first sentence already overflows.
const SENT_SPLIT = /(?<=[.!?])\s+/;
function fitSentences(str, max) {
  const s = (str || "").replace(/\s+/g, " ").trim();
  if (s.length <= max) return s;
  let out = "";
  for (const sent of s.split(SENT_SPLIT)) {
    const cand = out ? `${out} ${sent}` : sent;
    if (cand.length <= max) out = cand;
    else break;
  }
  return out || clip(s, max);
}

function parseBullets(content) {
  const strip = (s) => s.replace(/<[^>]+>/g, "");
  const dec = (s) =>
    s
      .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&nbsp;/g, " ");
  const bullets = [];
  for (const line of (content || "").split("\n")) {
    const t = line.trim();
    if (!t.startsWith("•")) continue;
    const raw = t.replace(/^•\s*/, "");
    const title = dec(strip(raw.match(/<b>([\s\S]*?)<\/b>/i)?.[1] ?? "")).trim();
    const body = dec(
      strip(raw.replace(/<b>[\s\S]*?<\/b>/i, "")
              .replace(/<a[^>]*>[\s\S]*?<\/a>/gi, "")
              .replace(/^\s*[—–-]+\s*/, ""))
    ).replace(/\s+/g, " ").trim();
    if (title) bullets.push({ title, body });
  }
  return bullets;
}

const EYEBROW = {
  daily:   "TODAY'S TOP SIGNAL",
  weekly:  "THIS WEEK'S TOP SIGNAL",
  markets: "TOP MARKET SIGNAL",
  alpha:   "TODAY'S TOP ALPHA",
  defi:    "TOP DEFI SIGNAL",
};

// ── Reusable bits ───────────────────────────────────────────────────────────
function Badge({ cat, large }) {
  return (
    <div
      style={{
        display: "flex", alignItems: "center", flexShrink: 0,
        backgroundColor: `${cat.color}1F`,
        border: `1px solid ${cat.color}59`,
        borderRadius: 5,
        padding: large ? "5px 12px" : "3px 9px",
      }}
    >
      <span
        style={{
          color: cat.color, fontFamily: "DM Mono",
          fontSize: large ? 13 : 10,
          letterSpacing: "0.14em", textTransform: "uppercase", lineHeight: 1,
        }}
      >
        {cat.label}
      </span>
    </div>
  );
}

function Arrow({ color, size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" style={{ marginLeft: 9 }}>
      <path
        d="M3 12h16M13 6l6 6-6 6"
        stroke={color} strokeWidth="2.4"
        strokeLinecap="round" strokeLinejoin="round"
      />
    </svg>
  );
}

// ── Route ─────────────────────────────────────────────────────────────────────
export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const type = searchParams.get("type") ?? "daily";
  const maxN = Math.max(1, Math.min(parseInt(searchParams.get("bullets") ?? "5", 10) || 5, 5));
  let dateStr = searchParams.get("date");

  if (!dateStr) dateStr = await latestDate();
  if (!dateStr) return new Response("No digest found", { status: 404 });
  // Reject malformed AND numeric-but-invalid dates (e.g. 2026-13-99) before the SQL date
  // cast — otherwise the cast throws and the card 500s on a public URL.
  if (!isValidDigestDate(dateStr)) return new Response("Bad date", { status: 400 });

  // Cache-first: serve the bot-rendered card (app/og_cache.py warms digest_og post-digest)
  // so the public route does NOT run satori on every request. Live render below is only the
  // fallback for a genuine cache miss (e.g. before the post-digest warm has run).
  const cached = await getOgCard(dateStr);
  if (cached?.png) {
    return new Response(cached.png, {
      status: 200,
      headers: { "Content-Type": cached.mime || "image/png", "Cache-Control": OG_CACHE_CONTROL },
    });
  }

  const digest = await getDigest(dateStr);
  if (!digest) return new Response(`No digest for ${dateStr}`, { status: 404 });

  const all = parseBullets(digest.content);
  if (!all.length) return new Response("No bullets in digest", { status: 404 });

  // ── Rank by importance score (highest first); ties / unscored keep digest order ──
  const analyses = await getBulletAnalyses(dateStr);
  const ranked = all
    .map((b, i) => ({ ...b, score: analyses[b.title]?.importanceScore ?? null, _i: i }))
    .sort((a, b) => {
      const sa = a.score ?? -1, sb = b.score ?? -1;
      return sb !== sa ? sb - sa : a._i - b._i;
    });

  const shown = ranked.slice(0, maxN);
  const moreCount = all.length - shown.length;

  const hero = shown[0];
  const rest = shown.slice(1);

  const heroCat = detectCat(hero.title, hero.body);
  const hl = hero.title.length;
  // Single-line hero headline so the factual detail (up to 3 lines) below has room.
  const heroSize = hl <= 18 ? 54 : hl <= 26 ? 46 : hl <= 34 ? 40 : 34;
  const heroTitle = clip(hero.title, 42);
  const heroDetail = fitSentences(hero.body, 300); // the factual "what happened", up to 3 lines

  // Per-row detail wraps to 2 lines (3 when rows are taller); fitSentences keeps it whole.
  const sc = rest.length;
  const secSize = sc <= 2 ? 30 : sc === 3 ? 27 : 25;
  const secClip = sc <= 2 ? 50 : 44;
  const detLines = sc <= 2 ? 3 : 2; // clamp so a long detail never bleeds into the next row
  const detClip = sc <= 2 ? 340 : 220; // sentence budget ≈ detLines worth of text

  const dateLabel = fmtDate(dateStr);
  const eyebrow = EYEBROW[type] ?? EYEBROW.daily;

  const [fonts, assets] = await Promise.all([getFonts(), getAssets()]);

  return new ImageResponse(
    (
      <div
        style={{
          width: W, height: H,
          backgroundColor: BG,
          display: "flex", flexDirection: "column",
          overflow: "hidden",
          fontFamily: "Raleway",
        }}
      >
        {/* ══ HEADER ════════════════════════════════════════════════════════ */}
        <div
          style={{
            display: "flex", flexDirection: "row",
            alignItems: "center", justifyContent: "space-between",
            padding: `0 ${PAD}px`, height: HEADER_H, flexShrink: 0,
          }}
        >
          {/* Brand + date (date is a first-class element beside the logo) */}
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <img src={assets.falcon} width={34} height={34} style={{ objectFit: "contain" }} />
            <span
              style={{
                color: ACCENT, fontSize: 23, fontWeight: 800,
                letterSpacing: "0.2em", fontFamily: "Raleway", lineHeight: 1,
              }}
            >
              HORYON
            </span>
            <div style={{ width: 1, height: 24, backgroundColor: "rgba(255,255,255,0.14)" }} />
            <span
              style={{
                color: ACCENT, fontSize: 14, fontFamily: "DM Mono",
                letterSpacing: "0.12em", lineHeight: 1,
              }}
            >
              {dateLabel}
            </span>
          </div>

          {/* Subtitle — muted, right */}
          <span
            style={{
              color: TEXT4, fontSize: 10.5, fontFamily: "DM Mono",
              letterSpacing: "0.22em", textTransform: "uppercase", lineHeight: 1,
            }}
          >
            CRYPTO INTELLIGENCE FEED
          </span>
        </div>

        {/* ══ GOLD BAR ══════════════════════════════════════════════════════ */}
        <div style={{ width: W, height: GOLD_BAR, backgroundColor: ACCENT, flexShrink: 0 }} />

        {/* ══ HERO — top-scored signal ══════════════════════════════════════ */}
        <div
          style={{
            display: "flex", flexDirection: "row", alignItems: "stretch",
            padding: `0 ${PAD}px`, height: HERO_H, flexShrink: 0,
            position: "relative", overflow: "hidden",
          }}
        >
          {/* Gold accent left border */}
          <div
            style={{
              width: 5, borderRadius: 3, backgroundColor: ACCENT,
              marginTop: 30, marginBottom: 30, flexShrink: 0,
            }}
          />

          <div
            style={{
              flex: 1, marginLeft: 26,
              display: "flex", flexDirection: "column", justifyContent: "center", gap: 12,
            }}
          >
            {/* Eyebrow + hero category badge */}
            <div
              style={{
                display: "flex", flexDirection: "row",
                alignItems: "center", justifyContent: "space-between",
              }}
            >
              <span
                style={{
                  color: ACCENT, fontSize: 14, fontFamily: "DM Mono",
                  letterSpacing: "0.24em", textTransform: "uppercase", lineHeight: 1,
                }}
              >
                {eyebrow}
              </span>
              <Badge cat={heroCat} large />
            </div>

            {/* Hero headline (single line) */}
            <span
              style={{
                color: TEXT, fontSize: heroSize, fontWeight: 800, fontFamily: "Raleway",
                textTransform: "uppercase", letterSpacing: "-0.01em", lineHeight: 1.03,
                whiteSpace: "nowrap", overflow: "hidden",
              }}
            >
              {heroTitle}
            </span>

            {/* Hero factual detail — the actual news (numbers, names), up to 3 lines */}
            {heroDetail && (
              <span
                style={{
                  display: "-webkit-box", WebkitBoxOrient: "vertical", WebkitLineClamp: 3,
                  overflow: "hidden", color: TEXT2, fontSize: 22, fontWeight: 500,
                  fontFamily: "Raleway", lineHeight: 1.32,
                }}
              >
                {heroDetail}
              </span>
            )}
          </div>
        </div>

        {/* ══ HAIRLINE ══════════════════════════════════════════════════════ */}
        <div style={{ width: W, height: DIVIDER, backgroundColor: "rgba(255,255,255,0.08)", flexShrink: 0 }} />

        {/* ══ SIGNAL LIST ═══════════════════════════════════════════════════ */}
        <div
          style={{
            height: SIGNAL_H, flexShrink: 0,
            display: "flex", flexDirection: "column",
            position: "relative", overflow: "hidden",
          }}
        >
          {/* Subtle falcon watermark, bleeding off the right edge */}
          <img
            src={assets.falcon}
            style={{
              position: "absolute", right: -70, top: "50%", marginTop: -200,
              width: 400, height: 400, objectFit: "contain", opacity: 0.03,
            }}
          />

          {rest.map((b, idx) => {
            const cat = detectCat(b.title, b.body);
            const isLast = idx === rest.length - 1;
            return (
              <div
                key={idx}
                style={{
                  flex: 1,
                  display: "flex", flexDirection: "row", alignItems: "center",
                  padding: `0 ${PAD}px`,
                  borderBottom: isLast ? "none" : "1px solid rgba(255,255,255,0.06)",
                  position: "relative", overflow: "hidden",
                }}
              >
                {/* Category accent stripe (full row height) */}
                <div
                  style={{
                    width: 3, height: 44, borderRadius: 2,
                    backgroundColor: cat.color, opacity: 0.7,
                    marginRight: 22, flexShrink: 0,
                  }}
                />

                {/* Headline + factual detail (two clean lines, no ellipsis) */}
                <div
                  style={{
                    flex: 1, display: "flex", flexDirection: "column",
                    justifyContent: "center", gap: 4, overflow: "hidden",
                  }}
                >
                  <span
                    style={{
                      color: TEXT, fontSize: secSize, fontWeight: 800,
                      fontFamily: "Raleway", textTransform: "uppercase",
                      letterSpacing: "0.005em", lineHeight: 1.08,
                      whiteSpace: "nowrap", overflow: "hidden",
                    }}
                  >
                    {clip(b.title, secClip)}
                  </span>
                  {b.body && (
                    <span
                      style={{
                        display: "-webkit-box", WebkitBoxOrient: "vertical",
                        WebkitLineClamp: detLines, overflow: "hidden",
                        color: TEXT3, fontSize: 16.5, fontWeight: 500,
                        fontFamily: "Raleway", lineHeight: 1.32,
                      }}
                    >
                      {fitSentences(b.body, detClip)}
                    </span>
                  )}
                </div>

                {/* Category badge, inline right */}
                <div style={{ display: "flex", marginLeft: 18, flexShrink: 0 }}>
                  <Badge cat={cat} />
                </div>
              </div>
            );
          })}
        </div>

        {/* ══ GOLD RULE ═════════════════════════════════════════════════════ */}
        <div style={{ width: W, height: FOOTER_RULE, backgroundColor: "rgba(212,175,55,0.30)", flexShrink: 0 }} />

        {/* ══ FOOTER ════════════════════════════════════════════════════════ */}
        <div
          style={{
            display: "flex", flexDirection: "row",
            alignItems: "center", justifyContent: "space-between",
            padding: `0 ${PAD}px`, height: FOOTER_H, flexShrink: 0,
          }}
        >
          {/* CTA */}
          <div style={{ display: "flex", flexDirection: "row", alignItems: "center" }}>
            <span
              style={{
                color: GOLD_CTA, fontSize: 14, fontFamily: "DM Mono",
                letterSpacing: "0.16em", textTransform: "uppercase", lineHeight: 1,
              }}
            >
              FULL INTEL AT HORYON.AI
            </span>
            <Arrow color={GOLD_CTA} />
          </div>

          {/* "+ N more" count, else tagline */}
          <span
            style={{
              color: TEXT3, fontSize: 11.5, fontFamily: "DM Mono",
              letterSpacing: "0.16em", textTransform: "uppercase", lineHeight: 1,
            }}
          >
            {moreCount > 0 ? `+ ${moreCount} more signals today` : "THE MARKET MOVES — WE SEE IT FIRST"}
          </span>
        </div>
      </div>
    ),
    { width: W, height: H, fonts, headers: { "Cache-Control": OG_CACHE_CONTROL } }
  );
}
