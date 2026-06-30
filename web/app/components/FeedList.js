import BulletItem from "./BulletItem";
import PodcastFeedItem from "./PodcastFeedItem";

// The daily feed's list body — renders the filtered+sorted `entries` (news rows
// and inline podcast rows) and wires per-row selection/cursor + tag search. The
// feed owns selection state; this is just the mapping.
export default function FeedList({ entries, selected, cursor, onSelect, onTagSearch, listRef }) {
  return (
    <ul className="bullets" ref={listRef}>
      {entries.map((e, i) => (
        e.kind === "podcast" ? (
          <PodcastFeedItem
            key={`pod:${e.podcast.video_id}`}
            podcast={e.podcast}
            selected={selected === i}
            cursor={cursor === i && selected !== i}
            onSelect={() => onSelect(i)}
          />
        ) : (
          <BulletItem
            key={`news:${i}:${e.b.title}`}
            title={e.b.title}
            body={e.b.body}
            hack={e.b.hack}
            src={e.b.src}
            link={e.b.link}
            ts={e.b.ts}
            projectHint={e.hint}
            importanceScore={e.score}
            sourceCount={e.sourceCount}
            selected={selected === i}
            cursor={cursor === i && selected !== i}
            onSelect={() => onSelect(i)}
            onTagSearch={onTagSearch}
          />
        )
      ))}
    </ul>
  );
}
