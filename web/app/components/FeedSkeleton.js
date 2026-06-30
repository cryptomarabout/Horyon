// Instant route-level loading state for the daily feed. Rendered by the App Router
// loading.js boundaries (app/loading.js + app/d/[date]/loading.js) the moment a date
// navigation starts, so stepping between days shows a shimmer skeleton in the feed
// column instead of freezing the previous page until the server render lands.
// Pure markup — mirrors the BulletFeed grid (feed-left list + empty feed-right) so the
// layout doesn't shift when the real content swaps in.

function SkBullet({ wide }) {
  return (
    <li className="bullet sk-bullet" aria-hidden="true">
      <div className="bullet-importance sk-bone" />
      <div className="bullet-layout">
        <div className="bullet-main">
          <div className="sk-bone sk-title" style={{ width: wide ? "82%" : "64%" }} />
          <div className="sk-bone sk-line" style={{ width: "96%" }} />
          <div className="sk-bone sk-line" style={{ width: wide ? "70%" : "88%" }} />
        </div>
        <div className="bullet-aside">
          <div className="sk-bone sk-badge" />
        </div>
      </div>
    </li>
  );
}

export default function FeedSkeleton() {
  const rows = [true, false, true, false, true, false, true];
  return (
    <div className="feed-grid" aria-busy="true">
      <div className="feed-left">
        <div className="feed-scroll">
          <div className="feed-head">
            <div className="feed-head-top">
              <div className="sk-bone sk-datenav" />
              <div className="sk-bone sk-chip" style={{ marginLeft: "auto", width: 84 }} />
              <div className="sk-bone sk-chip" style={{ width: 64 }} />
            </div>
          </div>
          <ul className="bullets">
            {rows.map((wide, i) => <SkBullet key={i} wide={wide} />)}
          </ul>
        </div>
      </div>
      <div className="feed-right" />
    </div>
  );
}
