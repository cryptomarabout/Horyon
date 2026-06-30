import { getNarrativesWithSignals } from "../../lib/db";
import NarrativeView from "../components/NarrativeView";

export const dynamic = "force-dynamic";

const _NARR_DESC = "Research on the themes moving digital-asset markets. Coverage compiled across 100+ sources by Horyon's intelligence pipeline, with momentum, conviction and cited developments for each brief.";

export const metadata = {
  // Bare label — the root layout's title template appends " · Horyon" (avoids a doubled brand).
  title: "Research",
  description: _NARR_DESC,
  alternates: { canonical: "https://app.horyon.xyz/narratives" },
  openGraph: {
    type: "website",
    title: "Research · Horyon",
    description: _NARR_DESC,
    url: "https://app.horyon.xyz/narratives",
  },
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
