import { NextResponse } from "next/server";
import { chatComplete } from "../../../lib/llm";

// Mirrors app/prompts.py BULLET_ANALYST_SYSTEM. This fallback runs only on a cache miss
// and receives no DB context, so it must lean on the headline/summary and refuse to invent.
const SYSTEM = `You are a concise, factual crypto markets analyst. \
Given a news headline and summary, write 3–4 sentences of additional context: \
background on the project or event, why it matters to the market, and one concrete thing to watch. \
CRITICAL: Do NOT invent numbers, prices, TVL figures, dates, version strings, launch events, or history. \
You are given no verified database context here, so base the analysis strictly on what the headline and \
summary state plus widely-established background you are certain of — when unsure, stay qualitative rather \
than stating a specific figure. Be direct. No bullet points. No headers.`;

export async function POST(req) {
  let title, body;
  try {
    ({ title, body } = await req.json());
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  if (!title?.trim()) {
    return NextResponse.json({ error: "title is required" }, { status: 400 });
  }

  const userMsg = body?.trim()
    ? `Headline: ${title}\n\nSummary: ${body}`
    : `Headline: ${title}`;

  try {
    const { content } = await chatComplete({ system: SYSTEM, user: userMsg, max_tokens: 350, temperature: 0.5 });
    return NextResponse.json({ content });
  } catch (err) {
    console.error("details route error:", err?.message ?? err);
    return NextResponse.json(
      { error: "AI request failed" },
      { status: 502 }
    );
  }
}
