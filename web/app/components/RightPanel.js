"use client";

import { useState, useEffect } from "react";
import { getDomain, fmtShortDate } from "../../lib/format";
import { ExtIcon } from "./icons";
import EmptyState from "./ui/EmptyState";
import PanelHeader from "./ui/PanelHeader";
import PanelBody from "./ui/PanelBody";
import BulletPanel   from "./panels/BulletPanel";
import SearchPanel   from "./panels/SearchPanel";
import PodcastPanel  from "./panels/PodcastPanel";
import NarrativePanel from "./panels/NarrativePanel";
import WeeklyPanel   from "./panels/WeeklyPanel";

// ── Related article detail overlay — sits above any main panel content ──────
function RelatedDetailLayer({ article, onBack, onClose }) {
  const domain = getDomain(article.link);
  return (
    <>
      <PanelHeader
        onClose={onClose}
        below={article.link && (
          <a href={article.link} target="_blank" rel="noreferrer" className="panel-src-link">
            <span>{domain ?? "Source"}</span>
            <ExtIcon size={9} />
          </a>
        )}
      >
        <button className="panel-back-btn" onClick={onBack} aria-label="Back to article">
          ← Back
        </button>
      </PanelHeader>
      <PanelBody>
        <div className="related-layer-date">{fmtShortDate(article.date)}</div>
        <h3 className="related-layer-title">{article.title}</h3>
        {article.body
          ? <p className="related-layer-body">{article.body}</p>
          : <p style={{ fontSize: "11px", color: "var(--text-4)" }}>No additional content.</p>
        }
      </PanelBody>
    </>
  );
}

// ── Panel router — picks the right sub-panel based on props ─────────────────
export default function RightPanel({ bullet, hint, cachedAnalysis, onClose, weekly, podcast, narrative, search, date }) {
  // relatedView is shared between BulletPanel (RelatedArticle click) and
  // WeeklyPanel (KeyStoryLine click) — both surface the same overlay.
  const [relatedView, setRelatedView] = useState(null);

  // Reset the overlay whenever the main content context changes.
  useEffect(() => {
    setRelatedView(null);
  }, [bullet?.title, weekly?.week_start, podcast?.video_id, narrative?.slug, search?.keyword]);

  let content;
  if (bullet) {
    content = (
      <BulletPanel
        bullet={bullet}
        hint={hint}
        cachedAnalysis={cachedAnalysis}
        onClose={onClose}
        date={date}
        onOpenArticle={setRelatedView}
      />
    );
  } else if (search?.keyword) {
    content = <SearchPanel search={search} />;
  } else if (narrative) {
    content = <NarrativePanel narrative={narrative} onClose={onClose} />;
  } else if (podcast) {
    content = <PodcastPanel podcast={podcast} onClose={onClose} />;
  } else if (weekly?.content) {
    content = <WeeklyPanel weekly={weekly} onClose={onClose} onOpenArticle={setRelatedView} />;
  } else {
    content = (
      <EmptyState glyph="◈">Select a story to view<br />project data &amp; analysis</EmptyState>
    );
  }

  const animKey = bullet?.title ?? search?.keyword ?? narrative?.slug ?? podcast?.video_id ?? weekly?.week_start ?? "empty";

  return (
    <div className="panel-container">
      <div key={animKey} className="panel-anim">
        {content}
      </div>
      {relatedView && (
        <div className="panel-layer">
          <RelatedDetailLayer
            article={relatedView}
            onBack={() => setRelatedView(null)}
            onClose={onClose}
          />
        </div>
      )}
    </div>
  );
}
