"use client";

import { MONTHS_SHORT } from "../../../lib/format";
import { ExtIcon } from "../icons";
import PanelSection from "../ui/PanelSection";
import PanelHeader from "../ui/PanelHeader";
import PanelBody from "../ui/PanelBody";
import BulletLines from "../ui/BulletLines";
import ChipRow from "../ui/ChipRow";

const POD_SENT = {
  bullish: { label: "Bullish", cls: "pod-badge--bull", glyph: "▲" },
  bearish: { label: "Bearish", cls: "pod-badge--bear", glyph: "▼" },
  neutral: { label: "Neutral", cls: "pod-badge--neut", glyph: "–" },
  mixed:   { label: "Mixed",   cls: "pod-badge--mix",  glyph: "◇" },
};

function fmtPodDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return `${MONTHS_SHORT[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
}

export default function PodcastPanel({ podcast, onClose }) {
  const a    = podcast.analysis || {};
  const sent = POD_SENT[a.sentiment] || POD_SENT.mixed;
  return (
    <>
      <PanelHeader
        onClose={onClose}
        below={
          <div className="pod-meta-row">
            <span className={`pod-badge ${sent.cls}`}>
              <span aria-hidden="true">{sent.glyph}</span> {sent.label}
            </span>
            <span className="pod-date">{fmtPodDate(podcast.published_at)}</span>
            {podcast.url && (
              <a href={podcast.url} target="_blank" rel="noreferrer"
                className="panel-src-link" onClick={e => e.stopPropagation()}>
                <span>YouTube</span>
                <ExtIcon size={9} />
              </a>
            )}
          </div>
        }
      >
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "3px" }}>
          <span className="pw-eyebrow">Podcast Intel · {podcast.channel}</span>
          <h2 className="panel-title panel-title--pod">{podcast.title}</h2>
        </div>
      </PanelHeader>

      <PanelBody>
        {a.tldr && (
          <PanelSection label="Summary">
            <p className="panel-ai-text">{a.tldr}</p>
          </PanelSection>
        )}
        <BulletLines label="Key Claims"  items={a.notable_claims} />
        <BulletLines label="Predictions" items={a.predictions} />
        <ChipRow     label="Themes"      items={a.themes} />
        <ChipRow     label="Entities"    items={a.entities} />
        {a.guests?.length > 0 && (
          <PanelSection label="Guests">
            <p className="panel-ai-text">{a.guests.join(", ")}</p>
          </PanelSection>
        )}
      </PanelBody>
    </>
  );
}
