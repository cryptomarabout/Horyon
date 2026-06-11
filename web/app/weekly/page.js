import { listWeeklyDigests } from "../../lib/db";
import WeeklyView from "../components/WeeklyView";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Weekly Macro · Horyon",
};

export default async function WeeklyPage() {
  let weeklies = [];
  try { weeklies = await listWeeklyDigests(); } catch { /* empty */ }

  return (
    <article className="digest">
      <WeeklyView weeklies={weeklies} />
    </article>
  );
}
