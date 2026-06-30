// Report cover/masthead — the "this is a publication" first impression.
// Serif title leads, a standfirst lifted from the week's market thesis, and a
// byline row carrying the date range, market-tone read, and publish date.
// Pure presentational; values are computed in WeeklyView.
export default function ReportMasthead({ title, dek, range, published, tone, issueNav }) {
  return (
    <header className="wr-masthead">
      <h1 className="wr-title">{title}</h1>
      {dek && <p className="wr-dek">{dek}</p>}

      <div className="wr-byline">
        {range && <span className="wr-byline-range">{range}</span>}
        {tone?.label && (
          <>
            <span className="sep">·</span>
            <span className="wr-tone" data-regime={tone.regime || ""}>
              <span className="wr-tone-dot" aria-hidden="true" />
              {tone.regime ? `${tone.label} · ${tone.regime}` : tone.label}
            </span>
          </>
        )}
        {published && (
          <>
            <span className="sep">·</span>
            <span>Published {published}</span>
          </>
        )}
      </div>

      {issueNav && <div style={{ marginTop: 20 }}>{issueNav}</div>}

      <div className="wr-rule" />
      <div className="wr-rule" />
    </header>
  );
}
