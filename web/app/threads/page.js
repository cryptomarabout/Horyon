import { redirect } from "next/navigation";
import { latestThreadDate } from "../../lib/db";

export const dynamic = "force-dynamic";

export default async function ThreadsHome() {
  let d = null;
  try { d = await latestThreadDate(); } catch { /* empty */ }
  if (d) redirect(`/threads/${d}`);
  return (
    <article className="digest">
      <div className="empty">
        No threads rendered yet. They build post-digest (or run{" "}
        <code>python3 -m app.threads</code>).
      </div>
    </article>
  );
}
