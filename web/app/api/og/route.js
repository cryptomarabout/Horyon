import { ImageResponse } from "next/og";
import { readFile } from "fs/promises";
import path from "path";
import { getDigest, latestDate } from "../../../lib/db";

export const dynamic = "force-dynamic";

// ── Canvas ───────────────────────────────────────────────────────────────────
const W = 1080;
const H = 1080;
const PAD = 40; // horizontal padding

// ── Fixed section heights ────────────────────────────────────────────────────
const HEADER_H = 52; // brand bar
const GOLD_BAR = 3; // gold rule under header
const HERO_H = 90; // "DAILY EDGE" + meta
const DIVIDER = 1; // hairline before signals
const FOOTER_H = 52; // bottom bar
const FOOTER_RULE = 1;
// SIGNAL_H is whatever remains:
const SIGNAL_H = H - HEADER_H - GOLD_BAR - HERO_H - DIVIDER - FOOTER_RULE - FOOTER_H;
// = 1080 - 52 - 3 - 90 - 1 - 1 - 52 = 881

// ── Brand palette ────────────────────────────────────────────────────────────
const BG = "#060606";
const TEXT = "#F0EDE6";
const TEXT2 = "#A7AFBC";
const TEXT3 = "#5E6B80";
const TEXT4 = "#394557";
const ACCENT = "#D4AF37";

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
  [/layer.?2|l2|rollup|zk.?proof|bridge|infra|quantum|post.?quantum|evm|signature/i, "INFRA", "#6366F1"],
  [/defi|tvl|liquidity|pool|yield|amm|dex|lend|borrow|vault|protocol/i, "DEFI", "#10B981"],
  [/\bai\b|llm|agent|model|machine.?learn|gpt|intelligence|fetch.*skill|skills.*launch/i, "AI", "#F59E0B"],
  [/browser|wallet|ux|app|platform|sdk|cli|launch|tool|comet/i, "PRODUCT", "#60A5FA"],
  [/btc|bitcoin|halv|miner|hash/i, "BITCOIN", "#F97316"],
  [/regul|(?<!\w)sec(?!\w)|cftc|legal|law|court|comply|compliance/i, "REGULATORY", "#EC4899"],
  [/market|price|bull|bear|rally|dump|ath|fund|vc|raise|capital|valuat/i, "MARKETS", ACCENT],
];

function detectCat(title, body) {
  const t = title + " " + body;
  for (const [re, label, color] of CATS) {
    if (re.test(t)) return { label, color };
  }
  return { label: "SIGNAL", color: ACCENT };
}

// ── Helpers ───────────────────────────────────────────────────────────────────
const DAYS = ["SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY"];
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

function fmtDate(dateStr) {
  const d = new Date(dateStr + "T00:00:00Z");
  return {
    dayName: DAYS[d.getUTCDay()],
    short: `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`,
  };
}

function trunc(str, max) {
  if (!str || str.length <= max) return str;
  const cut = str.slice(0, max);
  const sp = cut.lastIndexOf(" ");
  return (sp > max * 0.7 ? cut.slice(0, sp) : cut) + "…";
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

const HERO_TITLES = {
  daily:   ["DAILY EDGE"],
  weekly:  ["WEEKLY ROUNDUP"],
  markets: ["MARKET SIGNALS"],
  alpha:   ["ALPHA BRIEF"],
  defi:    ["DEFI PULSE"],
};

// ── Route ─────────────────────────────────────────────────────────────────────
export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const type = searchParams.get("type") ?? "daily";
  const maxN = Math.min(parseInt(searchParams.get("bullets") ?? "10", 10), 10);
  let dateStr = searchParams.get("date");

  if (!dateStr) dateStr = await latestDate();
  if (!dateStr) return new Response("No digest found", { status: 404 });

  const digest = await getDigest(dateStr);
  if (!digest) return new Response(`No digest for ${dateStr}`, { status: 404 });

  const bullets = parseBullets(digest.content).slice(0, maxN);
  if (!bullets.length) return new Response("No bullets in digest", { status: 404 });

  const n = bullets.length;

  // ── Hero title with dynamic themes ──────────────────────────────────────
  const themes = [...new Set(
    bullets.map(b => detectCat(b.title, b.body).label)
  )].slice(0, 3);
  const heroTitle = `${n} SIGNALS SHAPING CRYPTO`;
  const heroThemes = themes.join(" • ");

  // ── Adaptive sizing: Bloomberg-style hierarchy ─────────────────────────
  // Signal #1 is visually dominant (56px), #2+ are secondary (34px)
  const getSignalSize = (idx) => idx === 0 ? 56 : 34;
  const descSize     = n <= 6 ? 18 : 16;
  const showDesc     = n <= 9;
  const titleMaxLen  = n <= 4 ? 62 : n <= 6 ? 56 : n <= 8 ? 50 : 44;
  const descMaxLen   = n <= 4 ? 110 : n <= 6 ? 92 : 76;
  const padV         = n <= 4 ? 20 : n <= 6 ? 14 : n <= 8 ? 10 : 8;
  const cardGap      = 0; // spacing handled by separator lines

  const { dayName, short } = fmtDate(dateStr);

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
        {/* ══ HEADER BAR ════════════════════════════════════════════════════ */}
        <div
          style={{
            display: "flex", flexDirection: "row",
            alignItems: "center", justifyContent: "space-between",
            padding: `0 ${PAD}px`,
            height: HEADER_H, flexShrink: 0,
          }}
        >
          {/* Brand left */}
          <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
            <img src={assets.falcon} width={28} height={28} style={{ objectFit: "contain" }} />
            <span
              style={{
                color: ACCENT, fontSize: 17, fontWeight: 800,
                letterSpacing: "0.2em", fontFamily: "Raleway", lineHeight: 1,
              }}
            >
              HORYON
            </span>
            <span
              style={{
                color: TEXT4, fontSize: 7.5, fontFamily: "DM Mono",
                letterSpacing: "0.18em", textTransform: "uppercase", lineHeight: 1,
              }}
            >
              · CRYPTO INTELLIGENCE FEED
            </span>
          </div>

          {/* Right */}
          <span
            style={{
              color: ACCENT, fontSize: 12, fontFamily: "DM Mono",
              letterSpacing: "0.12em", lineHeight: 1,
            }}
          >
            HORYON.AI
          </span>
        </div>

        {/* ══ GOLD BAR ══════════════════════════════════════════════════════ */}
        <div style={{ width: W, height: GOLD_BAR, backgroundColor: ACCENT, flexShrink: 0 }} />

        {/* ══ HERO SECTION ══════════════════════════════════════════════════ */}
        <div
          style={{
            display: "flex", flexDirection: "row",
            alignItems: "center", justifyContent: "space-between",
            padding: `0 ${PAD}px`,
            height: HERO_H, flexShrink: 0,
            position: "relative",
            overflow: "hidden",
          }}
        >
          {/* Falcon — subtle brand watermark, right-anchored */}
          <img
            src={assets.falcon}
            style={{
              position: "absolute",
              right: PAD - 10,
              top: 0,
              height: HERO_H + 30,
              width: 140,
              objectFit: "contain",
              objectPosition: "right center",
              opacity: 0.03,
            }}
          />

          {/* Hero title + themes */}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <span
              style={{
                color: TEXT,
                fontSize: 64,
                fontWeight: 800,
                letterSpacing: "-0.04em",
                lineHeight: 0.95,
                textTransform: "uppercase",
              }}
            >
              {heroTitle}
            </span>

            <span
              style={{
                color: ACCENT,
                fontSize: 16,
                fontFamily: "DM Mono",
                letterSpacing: "0.12em",
                textTransform: "uppercase",
              }}
            >
              {heroThemes}
            </span>
          </div>

          {/* Meta — date right-aligned */}
          <div
            style={{
              display: "flex", flexDirection: "column",
              alignItems: "flex-end", gap: 5,
              paddingRight: 8,
            }}
          >
            <span
              style={{
                color: TEXT3, fontSize: 12, fontFamily: "DM Mono",
                letterSpacing: "0.1em", textTransform: "uppercase", lineHeight: 1,
              }}
            >
              {dayName}
            </span>
            <span
              style={{
                color: TEXT3, fontSize: 11, fontFamily: "DM Mono",
                letterSpacing: "0.08em", lineHeight: 1,
              }}
            >
              {short}
            </span>
          </div>
        </div>

        {/* ══ HAIRLINE BEFORE SIGNALS ════════════════════════════════════════ */}
        <div
          style={{
            width: W, height: DIVIDER,
            backgroundColor: "rgba(255,255,255,0.08)",
            flexShrink: 0,
          }}
        />

        {/* ══ SIGNAL LIST ═══════════════════════════════════════════════════ */}
        <div
          style={{
            height: SIGNAL_H, flexShrink: 0,
            display: "flex", flexDirection: "column",
            position: "relative",
            overflow: "hidden",
          }}
        >
          {/* Falcon watermark — behind signals, very subtle */}
          <img
            src={assets.falcon}
            style={{
              position: "absolute",
              right: -40,
              top: "50%",
              marginTop: -240,
              width: 480,
              height: 480,
              objectFit: "contain",
              opacity: 0.03,
            }}
          />

          {bullets.map((bullet, idx) => {
            const cat = detectCat(bullet.title, bullet.body);
            const titleText = trunc(bullet.title, titleMaxLen);
            const descText = showDesc && bullet.body ? trunc(bullet.body, descMaxLen) : "";
            const isLast = idx === n - 1;
            const isFirst = idx === 0;
            const titleSize = getSignalSize(idx);
            const flexGrow = isFirst ? 1.5 : 1;
            const bgColor = idx % 2 === 0 ? "rgba(255,255,255,0.015)" : "transparent";

            return (
              <div
                key={idx}
                style={{
                  flex: flexGrow,
                  display: "flex", flexDirection: "column",
                  justifyContent: "center",
                  padding: `${padV}px ${PAD}px`,
                  paddingLeft: 48,
                  backgroundColor: bgColor,
                  borderBottom: isLast
                    ? "none"
                    : "1px solid rgba(255,255,255,0.07)",
                  position: "relative",
                  overflow: "hidden",
                }}
              >
                {/* Large background number */}
                <span
                  style={{
                    position: "absolute",
                    left: 24,
                    top: 10,
                    fontSize: 56,
                    fontWeight: 800,
                    opacity: 0.10,
                    color: cat.color,
                    lineHeight: 1,
                  }}
                >
                  {String(idx + 1).padStart(2, "0")}
                </span>

                {/* Category accent stripe on left edge */}
                <div
                  style={{
                    position: "absolute",
                    left: 0, top: 0, bottom: 0,
                    width: 3,
                    backgroundColor: cat.color,
                    opacity: 0.55,
                  }}
                />

                {/* Title row */}
                <div
                  style={{
                    display: "flex", flexDirection: "row",
                    alignItems: "flex-start",
                    gap: 12,
                  }}
                >
                  {/* Title */}
                  <span
                    style={{
                      color: TEXT,
                      fontSize: titleSize,
                      fontWeight: 800,
                      fontFamily: "Raleway",
                      textTransform: "uppercase",
                      letterSpacing: "0.01em",
                      lineHeight: 1.05,
                      flex: 1,
                      overflow: "hidden",
                    }}
                  >
                    {titleText}
                  </span>

                  {/* Category tag — reduced visual weight */}
                  <div
                    style={{
                      display: "flex", flexShrink: 0, alignItems: "center",
                      backgroundColor: `${cat.color}14`,
                      border: `1px solid ${cat.color}40`,
                      borderRadius: 4,
                      padding: "2px 8px",
                      opacity: 0.8,
                    }}
                  >
                    <span
                      style={{
                        color: cat.color, fontSize: 8, fontFamily: "DM Mono",
                        letterSpacing: "0.12em", textTransform: "uppercase", lineHeight: 1,
                      }}
                    >
                      {cat.label}
                    </span>
                  </div>
                </div>

                {/* Description — single concise line */}
                {descText ? (
                  <div
                    style={{
                      display: "flex",
                      marginTop: 6,
                    }}
                  >
                    <span
                      style={{
                        color: TEXT2,
                        fontSize: descSize,
                        fontFamily: "Raleway",
                        fontWeight: 400,
                        lineHeight: 1.35,
                        overflow: "hidden",
                      }}
                    >
                      {descText}
                    </span>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>

        {/* ══ THIN GOLD RULE ════════════════════════════════════════════════ */}
        <div
          style={{
            width: W, height: FOOTER_RULE,
            backgroundColor: "rgba(212,175,55,0.30)",
            flexShrink: 0,
          }}
        />

        {/* ══ FOOTER ════════════════════════════════════════════════════════ */}
        <div
          style={{
            display: "flex", flexDirection: "row",
            alignItems: "center", justifyContent: "space-between",
            padding: `0 ${PAD}px`,
            height: FOOTER_H, flexShrink: 0,
          }}
        >
          <span
            style={{
              color: TEXT2, fontSize: 10, fontFamily: "DM Mono",
              letterSpacing: "0.1em", textTransform: "uppercase", lineHeight: 1,
            }}
          >
            THE MARKET MOVES. WE HELP YOU SEE IT FIRST.
          </span>
          <div style={{ display: "flex", gap: 20, alignItems: "center" }}>
            {["ACTIONABLE INTEL.", "REAL-TIME EDGE.", "BUILT FOR CRYPTO LEADERS."].map(
              (t) => (
                <span
                  key={t}
                  style={{
                    color: TEXT4, fontSize: 8, fontFamily: "DM Mono",
                    letterSpacing: "0.1em", textTransform: "uppercase", lineHeight: 1,
                  }}
                >
                  {t}
                </span>
              )
            )}
          </div>
        </div>
      </div>
    ),
    { width: W, height: H, fonts }
  );
}
