"use client";

import InfoTip from "../ui/InfoTip";

// ── Index methodology popover ────────────────────────────────────────────────
// The info affordance in the Atlas header (Index view only): explains how each
// column is derived — coverage is the universal spine, TVL/flows layer on where
// DeFiLlama lists them. Interaction (hover/click/Esc/outside-click) is the shared
// <InfoTip> primitive; this just supplies the content + `pl-info-*` styling.
export default function IndexInfo() {
  return (
    <InfoTip
      label="How the Index is built"
      title="Methodology"
      btnClassName="pl-info-btn"
      popClassName="pl-info-pop"
    >
      <p className="pl-info-title">How the Index is built</p>
      <p className="pl-info-lede">
        A ranked table of <strong>every</strong> tracked entity — protocols, chains,
        exchanges, funds and people. Coverage and connections are universal; TVL
        and flows layer on only where DeFiLlama lists them.
      </p>
      <dl className="pl-info-defs">
        <dt>TVL · 7d</dt>
        <dd>
          DeFiLlama total value locked, aggregated up to the brand (e.g. all Aave
          versions roll into Aave), with its 7-day change beneath — green inflow, red
          outflow. Protocols and tracked chains only; a “—” means no TVL exists for
          that entity type, not a gap.
        </dd>
        <dt>Sources</dt>
        <dd>
          Raw coverage — how many times the entity was mentioned across ~107 ingested
          sources (the bar is relative to the most-covered entity).
        </dd>
        <dt>Horyon</dt>
        <dd>
          Curated coverage — how many distinct Horyon <strong>daily-brief bullets</strong>
          have cited the entity, not just raw source mentions. The tag shows its
          narrative trajectory: Accelerating, Developing, Established, Moderating or
          Dormant.
        </dd>
        <dt>Connected</dt>
        <dd>
          Top co-mentioned entities plus the total co-mention degree — how central
          the entity is in the relationship graph.
        </dd>
      </dl>
      <p className="pl-info-foot">
        Click any column header to sort · click a row for the full entity brief.
      </p>
    </InfoTip>
  );
}
