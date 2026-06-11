"use client";

import { useState } from "react";

export default function DetailsButton({ title, body }) {
  const [state, setState] = useState("idle"); // idle | loading | open
  const [content, setContent] = useState("");
  const [isError, setIsError] = useState(false);

  async function toggle() {
    if (state === "loading") return;
    if (state === "open") { setState("idle"); return; }
    setState("loading");
    setIsError(false);
    try {
      const r = await fetch("/api/details", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, body }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "failed");
      setContent(data.content || "No additional details.");
      setIsError(false);
    } catch {
      setContent("Could not load details.");
      setIsError(true);
    }
    setState("open");
  }

  const isLoading = state === "loading";
  const isOpen    = state === "open";

  return (
    <>
      <button
        className={`btn-more${isOpen ? " open" : ""}`}
        onClick={toggle}
        disabled={isLoading}
      >
        {isLoading && <span className="btn-spinner" aria-hidden />}
        {isLoading ? "Loading…" : isOpen ? "↑ Close" : "Tell me more →"}
      </button>
      {isOpen && (
        <div className={`detail-panel${isError ? " detail-error" : ""}`}>
          {content}
        </div>
      )}
    </>
  );
}
