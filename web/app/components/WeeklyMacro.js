// Server component — no "use client", no interactivity needed.
// Receives pre-computed weekly digest HTML and renders it as a structured briefing card.

// ── Rotation tokens ────────────────────────────────────────────────────────
const ROTATION = {
  BTC:   { glyph: "₿", cls: "wm-btc",   label: "BTC Week"  },
  ETH:   { glyph: "Ξ", cls: "wm-eth",   label: "ETH Week"  },
  ALT:   { glyph: "◈", cls: "wm-alt",   label: "Alt Week"  },
  MIXED: { glyph: "≋", cls: "wm-mixed", label: "Mixed"     },
};

// ── HTML entity decoder (for section titles only — not full HTML) ──────────
const HTML_ENTITIES = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'" };
function decodeEntities(s) {
  return s.replace(/&[a-z#0-9]+;/gi, m => HTML_ENTITIES[m] ?? m);
}

// ── Week range formatter ───────────────────────────────────────────────────
function fmtWeekRange(start, end) {
  const MO = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const p = d => { const [y,m,day] = (d||"").split("-").map(Number); return {y,m,day}; };
  const s = p(start), e = p(end);
  if (!s.y) return "";
  if (s.m === e.m) return `${MO[s.m-1]} ${s.day}–${e.day}, ${s.y}`;
  return `${MO[s.m-1]} ${s.day} – ${MO[e.m-1]} ${e.day}, ${s.y}`;
}

// ── HTML parser ────────────────────────────────────────────────────────────
// Section headers in the stored HTML look like: <b>📊 Market Rotation</b>
// on their own line. We detect them by the leading emoji.
const SECTION_RE = /^<b>([📊🏆🔗🔥📰⚡][^<]*)<\/b>$/u;

function parseWeeklyContent(html) {
  if (!html) return [];
  // Strip the title line: "📅 <b>Weekly Crypto Macro · …</b>"
  const body = html.replace(/^📅\s*<b>[^<]*<\/b>\s*/i, "").trim();

  const sections = [];
  let header = null;
  let lines  = [];

  for (const raw of body.split(/\n/)) {
    const t = raw.trim();
    const m = t.match(SECTION_RE);
    if (m) {
      if (header !== null) sections.push({ header, lines });
      header = m[1].trim();
      lines  = [];
    } else if (header !== null && t) {
      lines.push(t);
    }
  }
  if (header !== null) sections.push({ header, lines });
  return sections;
}

// ── Content renderer ───────────────────────────────────────────────────────
// Converts lines into bullet items or paragraphs.
// Uses dangerouslySetInnerHTML only for the text parts (already sanitised by telegram_html).
function SectionBody({ lines }) {
  if (!lines.length) return null;
  return (
    <div className="wm-content">
      {lines.map((line, i) => {
        if (line.startsWith("•")) {
          const inner = line.slice(1).trim();
          return (
            <div key={i} className="wm-item">
              <span className="wm-dot" aria-hidden="true" />
              <span
                className="wm-item-text"
                dangerouslySetInnerHTML={{ __html: inner }}
              />
            </div>
          );
        }
        return (
          <p
            key={i}
            className="wm-para"
            dangerouslySetInnerHTML={{ __html: line }}
          />
        );
      })}
    </div>
  );
}

// ── Main export ────────────────────────────────────────────────────────────
export default function WeeklyMacro({ weekly }) {
  if (!weekly?.content) return null;

  const rotation = weekly.rotation || "MIXED";
  const rot      = ROTATION[rotation] || ROTATION.MIXED;
  const range    = fmtWeekRange(weekly.week_start, weekly.week_end);
  const sections = parseWeeklyContent(weekly.content);
  if (!sections.length) return null;

  return (
    <section className={`weekly-macro ${rot.cls}`} aria-label="Weekly macro digest">

      {/* ── Header bar ── */}
      <div className="wm-head">
        <div className="wm-head-left">
          <div className={`wm-badge ${rot.cls}`}>
            <span className="wm-badge-glyph" aria-hidden="true">{rot.glyph}</span>
            <span>{rot.label}</span>
          </div>
          {range && <span className="wm-range">{range}</span>}
        </div>
        <span className="wm-eyebrow" aria-hidden="true">Weekly Macro</span>
      </div>

      {/* ── Section grid ── */}
      <div className="wm-grid">
        {sections.map((s, i) => (
          <div key={i} className="wm-section">
            <h3 className="wm-section-title">{decodeEntities(s.header)}</h3>
            <SectionBody lines={s.lines} />
          </div>
        ))}
      </div>

    </section>
  );
}
