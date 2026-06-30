// Server component — no "use client", no interactivity needed.
import { fmtWeekRange } from "../../lib/format";

const ROTATION = {
  BTC:   { glyph: "₿", cls: "wm-btc",   label: "BTC Week"  },
  ETH:   { glyph: "Ξ", cls: "wm-eth",   label: "ETH Week"  },
  ALT:   { glyph: "◈", cls: "wm-alt",   label: "Alt Week"  },
  MIXED: { glyph: "≋", cls: "wm-mixed", label: "Mixed"     },
};

const HTML_ENTITIES = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'" };
function decodeEntities(s) {
  return s.replace(/&[a-z#0-9]+;/gi, m => HTML_ENTITIES[m] ?? m);
}

const SECTION_RE = /^<b>([📊🏆🔗🔥📰⚡][^<]*)<\/b>$/u;

function parseWeeklyContent(html) {
  if (!html) return [];
  const body = html.replace(/^📅\s*<b>[^<]*<\/b>\s*/i, "").trim();
  const sections = [];
  let header = null, lines = [];
  for (const raw of body.split(/\n/)) {
    const t = raw.trim();
    const m = t.match(SECTION_RE);
    if (m) {
      if (header !== null) sections.push({ header, lines });
      header = m[1].trim(); lines = [];
    } else if (header !== null && t) {
      lines.push(t);
    }
  }
  if (header !== null) sections.push({ header, lines });
  return sections;
}

function SectionBody({ lines }) {
  if (!lines.length) return null;
  return (
    <div className="wm-content">
      {lines.map((line, i) => {
        if (line.startsWith("•")) {
          return (
            <div key={i} className="wm-item">
              <span className="wm-dot" aria-hidden="true" />
              <span className="wm-item-text" dangerouslySetInnerHTML={{ __html: line.slice(1).trim() }} />
            </div>
          );
        }
        return <p key={i} className="wm-para" dangerouslySetInnerHTML={{ __html: line }} />;
      })}
    </div>
  );
}

export default function WeeklyMacro({ weekly }) {
  if (!weekly?.content) return null;
  const rot      = ROTATION[weekly.rotation || "MIXED"] || ROTATION.MIXED;
  const range    = fmtWeekRange(weekly.week_start, weekly.week_end);
  const sections = parseWeeklyContent(weekly.content);
  if (!sections.length) return null;

  return (
    <section className={`weekly-macro ${rot.cls}`} aria-label="Weekly macro digest">
      <div className="wm-head">
        <div className="wm-head-left">
          <div className={`wm-badge ${rot.cls}`}>
            <span className="wm-badge-glyph" aria-hidden="true">{rot.glyph}</span>
            <span>{rot.label}</span>
          </div>
          {range && <span className="wm-range">{range}</span>}
        </div>
        <span className="wm-eyebrow" aria-hidden="true">Weekly Briefs</span>
      </div>
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
