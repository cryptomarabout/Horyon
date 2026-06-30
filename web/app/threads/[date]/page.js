import { getThread, listThreadDates } from "../../../lib/db";
import ThreadView from "../../components/ThreadView";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Thread Composer · Horyon",
};

export default async function ThreadPage({ params }) {
  const { date } = params;
  let thread = null;
  let dates = [];
  try {
    [thread, dates] = await Promise.all([getThread(date), listThreadDates()]);
  } catch {
    /* render empty state */
  }

  return (
    <article className="digest">
      <ThreadView thread={thread} dates={dates} date={date} />
    </article>
  );
}
