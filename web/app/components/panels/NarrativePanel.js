"use client";

import { useState } from "react";
import Sparkline from "../research/Sparkline";
import {
  trajectoryMeta, momentumMultiple, convictionTier, coverageWindow,
  asOfLabel, evidenceMixLabel, cadenceSeries, cadenceLabels, typeMeta, timeAgo,
} from "../../../lib/narratives";
import { getDomain } from "../../../lib/format";
import { dedupeEntities, EntityAvatar } from "../EntityTag";
import PanelHeader from "../ui/PanelHeader";
import PanelBody from "../ui/PanelBody";
import PanelSection from "../ui/PanelSection";

const CITE_PREVIEW = 6;

function CitationRow({ s }) {
  const tm = typeMeta(s.signal_type);
  const ago = timeAgo(s.ts);
  const domain = s.url ? getDomain(s.url) : null;
  const Tag = s.url ? "a" : "div";
  const props = s.url ? { href: s.url, target: "_blank", rel: "noreferrer" } : {};
  return (
    <Tag className={`rsrch-cite rsrch-cite--${s.signal_type}`} {...props}>
      <span className="rsrch-cite-body">
        <span className="rsrch-cite-title">{s.title}</span>
        <span className="rsrch-cite-meta">
          <span className="rsrch-cite-type">{tm.label}</span>
          {domain && <span>· {domain}</span>}
          {ago && <span>· {ago}</span>}
          {s.source_count >= 2 && <span>· {s.source_count} sources</span>}
          {s.importance != null && <span className="rsrch-cite-imp">· ★{s.importance}</span>}
        </span>
      </span>
      {s.url && <span className="rsrch-cite-ext" aria-hidden>↗</span>}
    </Tag>
  );
}

function Stat({ k, v, vClass }) {
  return (
    <div className="rsrch-stat-cell">
      <span className="rsrch-stat-k">{k}</span>
      <span className={`rsrch-stat-v${vClass ? " " + vClass : ""}`}>{v}</span>
    </div>
  );
}

export default function NarrativePanel({ narrative, onClose }) {
  const n = narrative;
  const traj = trajectoryMeta(n.state);
  const caret = traj.dir === "up" ? "▲" : traj.dir === "down" ? "▼" : "—";
  const signals = n.signals || [];
  const cov = coverageWindow(n.first_seen, n.last_signal_at);
  const conv = convictionTier({ signalCount: n.signal_count, spanDays: cov?.spanDays || 0 });
  const mult = momentumMultiple(n.momentum_ratio);
  const cadence = cadenceSeries(signals, n.first_seen);
  const labels = cadenceLabels(signals, n.first_seen);
  const mixLabel = evidenceMixLabel(signals);
  const updated = asOfLabel(n.last_signal_at);
  const deduped = dedupeEntities(n.entities || []);

  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? signals : signals.slice(0, CITE_PREVIEW);

  const devs = `${n.signal_count} development${n.signal_count === 1 ? "" : "s"}`;
  const srcPhrase = n.source_count
    ? `, drawn from ${n.source_count} distinct source${n.source_count === 1 ? "" : "s"}`
    : "";

  return (
    <>
      <PanelHeader
        onClose={onClose}
        below={
          <div className="rsrch-byline">
            <span className="rsrch-byline-desk">Horyon Research</span>
            {updated && <><span className="rsrch-byline-dot">·</span><span>{updated}</span></>}
          </div>
        }
      >
        <div className="rsrch-brief-head">
          <div className="rsrch-brief-eyebrow">
            <span className="rsrch-sector">{n.sector}</span>
            <span className={`rsrch-traj rsrch-traj--${traj.cls}`}>
              <span className="rsrch-traj-caret" aria-hidden>{caret}</span>{traj.label}
            </span>
          </div>
          <h2 className="rsrch-brief-title">{n.label}</h2>
        </div>
      </PanelHeader>

      <PanelBody>
        <div className="rsrch-statband">
          <Stat k="Momentum" v={mult || "—"} />
          <Stat k="Developments" v={n.signal_count} />
          <Stat k="Coverage" v={cov ? cov.spanLabel : "—"} />
          <Stat k="Conviction" v={conv.label} vClass={`rsrch-conv--${conv.key}`} />
        </div>

        {n.thesis ? (
          <PanelSection label="Abstract" className="rsrch-section">
            <p className="rsrch-abstract">{n.thesis}</p>
          </PanelSection>
        ) : n.key_points?.length > 0 ? null : (
          <div className="rsrch-abstract-empty">
            Brief synthesis in progress — check back after the next pipeline run.
          </div>
        )}

        {n.key_points?.length > 0 && (
          <PanelSection label="Key points" className="rsrch-section">
            <ul className="rsrch-keypoints">
              {n.key_points.map((k, i) => (
                <li key={i}><span className="rsrch-kp-mark" aria-hidden>—</span><span>{k}</span></li>
              ))}
            </ul>
          </PanelSection>
        )}

        {cadence.length > 0 && (
          <PanelSection label="Activity" className="rsrch-section">
            <div className="rsrch-activity">
              <Sparkline data={cadence} labels={labels} vbWidth={260} height={34} />
              <div className="rsrch-activity-meta">
                {cov && <span>{cov.rangeLabel}</span>}
                {mixLabel && <span className="rsrch-activity-mix">{mixLabel}</span>}
              </div>
            </div>
          </PanelSection>
        )}

        {deduped.length > 0 && (
          <PanelSection label="Coverage universe" className="rsrch-section">
            <div className="rsrch-cov-grid">
              {deduped.slice(0, 10).map(e => (
                <span key={e.slug} className="rsrch-cov-chip">
                  <EntityAvatar
                    avatars={[e.avatar_cached ? `/api/avatar/${e.slug}` : null, e.logo_url].filter(Boolean)}
                    type={e.type || "other"}
                    name={e.display}
                    imgClass="rsrch-cov-logo"
                    monoClass="rsrch-cov-mono"
                  />
                  {e.display}
                </span>
              ))}
            </div>
          </PanelSection>
        )}

        {signals.length > 0 && (
          <PanelSection label="Source developments" count={signals.length} className="rsrch-section">
            <div className="rsrch-cites">
              {visible.map((s, i) => <CitationRow key={`${s.signal_ref}:${i}`} s={s} />)}
            </div>
            {signals.length > CITE_PREVIEW && (
              <button className="rsrch-more" onClick={() => setShowAll(v => !v)}>
                {showAll ? "Show fewer" : `All ${signals.length} developments`}
              </button>
            )}
          </PanelSection>
        )}

        {n.watch_next?.length > 0 && (
          <PanelSection label="What we're watching" className="rsrch-section">
            <ul className="rsrch-watch-list">
              {n.watch_next.map((w, i) => (
                <li key={i} className="rsrch-watch-card">
                  <span className="rsrch-watch-icon" aria-hidden>→</span>
                  <span className="rsrch-watch-text">{w}</span>
                </li>
              ))}
            </ul>
          </PanelSection>
        )}

        {n.contrarian && (
          <PanelSection label="Risks to the thesis" className="rsrch-section">
            <div className="rsrch-risk-callout">
              <p className="rsrch-risk-text">{n.contrarian}</p>
            </div>
          </PanelSection>
        )}

        <div className="rsrch-method">
          <span className="rsrch-method-k">Methodology</span>
          <p>
            Compiled automatically by Horyon's intelligence pipeline. {devs}
            {mixLabel ? ` (${mixLabel})` : ""}{cov ? ` clustered over ${cov.spanLabel}` : ""}
            {srcPhrase} by entity co-occurrence and embedding similarity. Momentum (ρ) compares the
            last 48 hours of weighted activity against a 21-day baseline; conviction reflects
            corroboration breadth and coverage persistence, not price. Generated research — not
            investment advice.
          </p>
        </div>
      </PanelBody>
    </>
  );
}
