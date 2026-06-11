import { getNarrativesWithSignals } from "../../lib/db";
import NarrativeView from "../components/NarrativeView";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Narratives · Horyon",
};

export default async function NarrativesPage() {
  let narratives = [];
  try { narratives = await getNarrativesWithSignals(); } catch { /* empty */ }

  return (
    <article className="digest">
      <NarrativeView narratives={narratives} />
    </article>
  );
}
