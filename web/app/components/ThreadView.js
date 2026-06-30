"use client";

import { useMemo, useState, useCallback } from "react";
import Link from "next/link";

// X counts any URL as 23 chars (t.co), and the poster appends " " + link to a
// body tweet → effective length = text + 24. The hook carries the OG image, no link.
const TWEET_HARD_MAX = 280;
const LINK_COST = 24;

function fmtDate(d) {
  if (!d) return "";
  const [y, m, day] = d.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, day)).toLocaleDateString("en-US", {
    weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC",
  });
}

function tweetLen(text, link) {
  return (text || "").length + (link ? LINK_COST : 0);
}

// One tweet row, Twitter-style, with an inline editor when `editing`.
function TweetCard({ n, total, isHook, text, link, score, ogPath, editing, onChange }) {
  const len = tweetLen(text, isHook ? null : link);
  const over = len > TWEET_HARD_MAX;
  const near = !over && len > TWEET_HARD_MAX - 20;
  const [copied, setCopied] = useState(false);

  const copy = useCallback(() => {
    const out = isHook ? text : `${text}${link ? " " + link : ""}`;
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(out).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    }).catch(() => {});
  }, [text, link, isHook]);

  return (
    <li className={`tw${isHook ? " tw--hook" : ""}`}>
      <div className="tw-gutter" aria-hidden>
        <img className="tw-avatar" src="/falcon.png" alt="" />
        {n < total && <span className="tw-line" />}
      </div>

      <div className="tw-body">
        <div className="tw-head">
          <span className="tw-name">Horyon</span>
          <span className="tw-handle">@Horyonhq</span>
          <span className="tw-dot">·</span>
          <span className="tw-idx">{isHook ? "Hook" : `${n - 1}/${total - 1}`}</span>
          {!isHook && score != null && (
            <span className="tw-score" title="Importance score">{score}</span>
          )}
        </div>

        {editing ? (
          <textarea
            className={`tw-edit${over ? " is-over" : ""}`}
            value={text}
            rows={isHook ? 3 : 2}
            onChange={(e) => onChange(e.target.value)}
            spellCheck
          />
        ) : (
          <p className="tw-text">{text}</p>
        )}

        {isHook && ogPath && (
          // eslint-disable-next-line @next/next/no-img-element
          <img className="tw-og" src={ogPath} alt="Social card preview" loading="lazy" />
        )}

        {!isHook && link && (
          <a className="tw-link" href={link} target="_blank" rel="noopener noreferrer">
            <span className="tw-link-glyph" aria-hidden>↗</span>
            <span className="tw-link-url">{link.replace(/^https?:\/\//, "").slice(0, 60)}</span>
          </a>
        )}

        <div className="tw-foot">
          <span className={`tw-count${over ? " is-over" : near ? " is-near" : ""}`}>
            {len}/{TWEET_HARD_MAX}
            {!isHook && link && <span className="tw-count-note"> · incl. link</span>}
          </span>
          <button type="button" className="tw-copy" onClick={copy}>
            {copied ? "Copied ✓" : "Copy"}
          </button>
        </div>
      </div>
    </li>
  );
}

export default function ThreadView({ thread, dates = [], date }) {
  // ── Date navigation across rendered threads ──────────────────────────────
  const idx = dates.findIndex((d) => d.date === date);
  const prev = idx >= 0 && idx < dates.length - 1 ? dates[idx + 1] : null; // older
  const next = idx > 0 ? dates[idx - 1] : null;                            // newer

  // ── Editable draft state (null when there is no thread) ──────────────────
  const [draft, setDraft] = useState(() =>
    thread
      ? { hook: thread.hook || "", tweets: (thread.tweets || []).map((t) => ({ ...t })), cta: thread.cta || "" }
      : null
  );
  const [editing, setEditing] = useState(false);
  const [status, setStatus] = useState(thread?.status || "pending");
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(null);
  const [err, setErr] = useState(null);
  const [copiedAll, setCopiedAll] = useState(null); // null | "ok" | "fail"

  // Same-origin OG path so it loads inside the basic-auth'd app regardless of
  // the absolute PUBLIC_BASE_URL stored on the row.
  const ogPath = useMemo(() => {
    if (thread?.og_image_url) return thread.og_image_url.replace(/^https?:\/\/[^/]+/, "");
    return `/api/og?date=${date}&type=daily&bullets=5`;
  }, [thread, date]);

  const dirty = useMemo(() => {
    if (!draft || !thread) return false;
    if (draft.hook !== (thread.hook || "")) return true;
    if (draft.cta !== (thread.cta || "")) return true;
    const orig = thread.tweets || [];
    if (draft.tweets.length !== orig.length) return true;
    return draft.tweets.some((t, i) => t.text !== (orig[i]?.text || ""));
  }, [draft, thread]);

  const setHook = (v) => setDraft((d) => ({ ...d, hook: v }));
  const setCta = (v) => setDraft((d) => ({ ...d, cta: v }));
  const setTweet = (i, v) =>
    setDraft((d) => ({ ...d, tweets: d.tweets.map((t, j) => (j === i ? { ...t, text: v } : t)) }));

  const patch = useCallback(async (payload, after) => {
    setSaving(true);
    setErr(null);
    try {
      const r = await fetch("/api/thread", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date, ...payload }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "save failed");
      after?.();
      setSavedAt(Date.now());
    } catch (e) {
      setErr(e.message || "save failed");
    } finally {
      setSaving(false);
    }
  }, [date]);

  const save = () => patch({ hook: draft.hook, tweets: draft.tweets, cta: draft.cta }, () => setEditing(false));
  const toggleStatus = () => {
    const nextStatus = status === "posted" ? "pending" : "posted";
    patch({ status: nextStatus }, () => setStatus(nextStatus));
  };
  // A blocked thread failed the modality safety gate. Marking it posted is disabled; the
  // operator must consciously unblock (→ pending) first, then post.
  const unblock = () => patch({ status: "pending" }, () => setStatus("pending"));

  const copyAll = async () => {
    if (!draft) return;
    const parts = [draft.hook, ...draft.tweets.map((t) => `${t.text}${t.link ? " " + t.link : ""}`)];
    if (draft.cta) parts.push(draft.cta);
    try {
      if (!navigator.clipboard) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(parts.join("\n\n"));
      setCopiedAll("ok");
    } catch {
      setCopiedAll("fail");
    }
    setTimeout(() => setCopiedAll(null), 1800);
  };

  // ── Empty state ──────────────────────────────────────────────────────────
  if (!thread || !draft) {
    return (
      <div className="thread-wrap">
        <ThreadNav date={date} prev={prev} next={next} />
        <div className="feed-empty">
          <div className="feed-empty-glyph" aria-hidden>🧵</div>
          <p>
            No thread rendered for {fmtDate(date)}.<br />
            Run <code>docker exec horyon-bot python3 -m app.threads --date {date}</code>.
          </p>
        </div>
      </div>
    );
  }

  const total = draft.tweets.length + 1; // hook + bullets
  const overCount = draft.tweets.filter((t) => tweetLen(t.text, t.link) > TWEET_HARD_MAX).length
    + (tweetLen(draft.hook, null) > TWEET_HARD_MAX ? 1 : 0);

  return (
    <div className="thread-wrap">
      <ThreadNav date={date} prev={prev} next={next} status={status} />

      <div className="thread-toolbar">
        <div className="thread-toolbar-left">
          <span className={`thread-status thread-status--${status}`}>
            {status === "posted" ? "Posted" : status === "blocked" ? "Blocked" : "Pending"}
          </span>
          <span className="thread-meta">
            {draft.tweets.length + 1} tweets
            {overCount > 0 && <span className="thread-warn"> · {overCount} over 280</span>}
          </span>
          {thread.model_used && <span className="thread-model">{thread.model_used}</span>}
        </div>
        <div className="thread-toolbar-right">
          <button type="button" className="thread-btn" onClick={copyAll}>
            {copiedAll === "ok" ? "Copied ✓" : copiedAll === "fail" ? "Copy failed" : "Copy all"}
          </button>
          <a className="thread-btn" href={ogPath} target="_blank" rel="noopener noreferrer">OG card</a>
          {editing ? (
            <>
              <button type="button" className="thread-btn" onClick={() => {
                setDraft({ hook: thread.hook, tweets: thread.tweets.map((t) => ({ ...t })), cta: thread.cta });
                setEditing(false);
              }}>Cancel</button>
              <button
                type="button"
                className="thread-btn thread-btn--primary"
                onClick={save}
                disabled={saving || !dirty}
              >
                {saving ? "Saving…" : "Save edits"}
              </button>
            </>
          ) : status === "blocked" ? (
            <>
              <button type="button" className="thread-btn thread-btn--danger" onClick={unblock} disabled={saving}>
                {saving ? "…" : "Unblock (override gate)"}
              </button>
              <button type="button" className="thread-btn thread-btn--primary" onClick={() => setEditing(true)}>
                Edit
              </button>
            </>
          ) : (
            <>
              <button type="button" className="thread-btn" onClick={toggleStatus} disabled={saving}>
                {status === "posted" ? "Mark pending" : "Mark posted"}
              </button>
              <button type="button" className="thread-btn thread-btn--primary" onClick={() => setEditing(true)}>
                Edit
              </button>
            </>
          )}
        </div>
      </div>

      {status === "blocked" && (
        <div className="thread-blocked-note">
          ⚠ This thread was held back by the modality safety gate (a development was
          overstated or mis-tensed). Review and edit it, then <b>Unblock</b> before posting.
        </div>
      )}

      {err && <div className="thread-error">{err}</div>}
      {savedAt && !err && !editing && <div className="thread-saved">Saved ✓</div>}

      <ul className="thread-list">
        <TweetCard
          n={1} total={total} isHook
          text={draft.hook} ogPath={ogPath}
          editing={editing} onChange={setHook}
        />
        {draft.tweets.map((t, i) => (
          <TweetCard
            key={i}
            n={i + 2} total={total}
            text={t.text} link={t.link} score={t.importance_score}
            editing={editing} onChange={(v) => setTweet(i, v)}
          />
        ))}
      </ul>

      {(draft.cta || editing) && (
        <div className="thread-cta">
          <span className="thread-cta-label">Closing / CTA</span>
          {editing ? (
            <textarea className="tw-edit" rows={2} value={draft.cta} onChange={(e) => setCta(e.target.value)} />
          ) : (
            <p>{draft.cta}</p>
          )}
        </div>
      )}
    </div>
  );
}

function ThreadNav({ date, prev, next }) {
  return (
    <div className="thread-nav">
      {prev ? (
        <Link className="thread-nav-step" href={`/threads/${prev.date}`} prefetch={false}>‹ {prev.date}</Link>
      ) : (
        <span className="thread-nav-step is-disabled">‹</span>
      )}
      <div className="thread-nav-title">
        <span className="thread-nav-eyebrow">Thread composer</span>
        <h1 className="thread-nav-date">{fmtDate(date)}</h1>
      </div>
      {next ? (
        <Link className="thread-nav-step" href={`/threads/${next.date}`} prefetch={false}>{next.date} ›</Link>
      ) : (
        <span className="thread-nav-step is-disabled">›</span>
      )}
    </div>
  );
}
