import { select } from "d3-selection";

// Shared D3 avatar fallback for graph nodes: mirrored logo → real logo →
// Twitter pic → (remove → the monogram underneath shows). Walks `cands` with a
// backoff retry, identical for the board + network views. The caller appends the
// clipPath + sizes/positions the <image> (the board defers sizing to its layout
// pass), then hands the image selection here to wire up load/error handling.
export function wireAvatarFallback(img, g, cands) {
  let idx = 0, tries = 0;
  img.on("load", () => g.classed("has-logo", true));
  img.on("error", function () {
    const self = select(this);
    if (tries < 2) {
      tries += 1;
      const url = cands[idx];
      setTimeout(() => self.attr("href",
        `${url}${url.includes("?") ? "&" : "?"}_r=${tries}`), 500 * tries);
      return;
    }
    tries = 0; idx += 1;
    if (idx < cands.length) self.attr("href", cands[idx]);
    else { g.classed("has-logo", false); self.remove(); }
  });
}
