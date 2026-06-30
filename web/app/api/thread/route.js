import { NextResponse } from "next/server";
import { updateThread, getThread } from "../../../lib/db";
import { badTweet } from "../../../lib/validateThread.mjs";

export const dynamic = "force-dynamic";

// PATCH /api/thread — persist hand-edits from the thread composer.
// Body: { date, hook?, tweets?, cta?, status? }. `tweets` is the whole array.
export async function PATCH(req) {
  let body;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON" }, { status: 400 });
  }

  const { date, hook, tweets, cta, status } = body || {};
  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return NextResponse.json({ error: "date (YYYY-MM-DD) required" }, { status: 400 });
  }
  // The composer only sets pending|posted; `blocked` is reserved for the Python
  // fail-closed safety gate and is never settable from the web UI.
  if (status !== undefined && !["pending", "posted"].includes(status)) {
    return NextResponse.json({ error: "status must be pending|posted" }, { status: 400 });
  }
  if (hook !== undefined && typeof hook !== "string") {
    return NextResponse.json({ error: "hook must be a string" }, { status: 400 });
  }
  if (cta !== undefined && typeof cta !== "string") {
    return NextResponse.json({ error: "cta must be a string" }, { status: 400 });
  }
  if (tweets !== undefined) {
    if (!Array.isArray(tweets)) {
      return NextResponse.json({ error: "tweets must be an array" }, { status: 400 });
    }
    for (const t of tweets) {
      const why = badTweet(t);
      if (why) return NextResponse.json({ error: why }, { status: 400 });
    }
  }

  try {
    // Fail-closed contract: a thread the safety gate marked `blocked` must be explicitly
    // unblocked (status → pending) before it can ever be marked posted.
    if (status === "posted") {
      const cur = await getThread(date);
      if (cur?.status === "blocked") {
        return NextResponse.json(
          { error: "thread is blocked by the safety gate — unblock it first" },
          { status: 409 }
        );
      }
    }

    const res = await updateThread(date, { hook, tweets, cta, status });
    if (!res) return NextResponse.json({ error: "thread not found" }, { status: 404 });
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json({ error: "update failed" }, { status: 500 });
  }
}
