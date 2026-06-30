"use client";

import { useMemo, useState } from "react";
import WeeklySections from "../WeeklyReader";
import PanelHeader from "../ui/PanelHeader";
import PanelBody from "../ui/PanelBody";
import {
  fmtWeekRange, rotMeta, PW_TABS, parseWeeklySections, sortVisibleSections, availableTabs,
} from "../../../lib/weekly";

// Legacy right-panel weekly reader. The Weekly *page* now renders the report
// inline (see WeeklyView); this panel stays for any context that still opens a
// weekly in the shared RightPanel. Parsing + line rendering are shared via
// lib/weekly + WeeklyReader so there is one source of truth.
export default function WeeklyPanel({ weekly, onClose, onOpenArticle }) {
  const [activeTab, setActiveTab] = useState("all");
  const rot      = rotMeta(weekly.rotation);
  const range    = fmtWeekRange(weekly.week_start, weekly.week_end);
  const sections = useMemo(() => parseWeeklySections(weekly.content), [weekly.content]);
  const tabs     = useMemo(() => availableTabs(sections), [sections]);
  const visible  = useMemo(() => sortVisibleSections(sections, activeTab), [sections, activeTab]);

  return (
    <>
      <PanelHeader
        onClose={onClose}
        tabbed
        below={
          <div className="pw-tabs" role="tablist" aria-label="Weekly sections">
            {(tabs.length > 1 ? tabs : PW_TABS).map(tab => (
              <button
                key={tab.id}
                role="tab"
                aria-selected={activeTab === tab.id}
                className={`pw-tab${activeTab === tab.id ? " active" : ""}`}
                onClick={() => setActiveTab(tab.id)}
              >
                {tab.label}
              </button>
            ))}
          </div>
        }
      >
        <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "baseline", gap: "7px" }}>
          <span className="pw-eyebrow">Weekly Briefs</span>
          <span className="pw-range">{range}</span>
        </div>
      </PanelHeader>

      <PanelBody>
        <WeeklySections sections={visible} weekly={weekly} onOpenArticle={onOpenArticle} />
      </PanelBody>
    </>
  );
}
