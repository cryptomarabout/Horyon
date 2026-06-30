import FeedSkeleton from "./components/FeedSkeleton";

// Shown on a cold load of "/" while the latest digest date is resolved + redirected.
export default function Loading() {
  return (
    <article className="digest">
      <FeedSkeleton />
    </article>
  );
}
