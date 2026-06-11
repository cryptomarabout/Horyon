import { redirect } from "next/navigation";
import { latestDate } from "../lib/db";

export const dynamic = "force-dynamic";

export default async function Home() {
  const d = await latestDate();
  if (d) redirect(`/d/${d}`);
  return <div className="empty">No digests yet. Check back after the next run.</div>;
}
