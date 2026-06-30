import FeedSkeleton from "../../components/FeedSkeleton";

// Shown instantly while the server renders a different date's digest (force-dynamic).
export default function Loading() {
  return (
    <article className="digest">
      <FeedSkeleton />
    </article>
  );
}
