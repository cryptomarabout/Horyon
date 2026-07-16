// PWA manifest (Next metadata route → /manifest.webmanifest). Makes Horyon installable
// on mobile home screens with brand-correct icons + theme colors, so the audio briefing
// (the most differentiated feature, consumed on mobile) opens in a standalone shell.
// Same-origin only — served under the CSP's default-src 'self'.
export default function manifest() {
  return {
    name: "Horyon · Crypto Market Intelligence",
    short_name: "Horyon",
    description:
      "The day's onchain, DeFi and market news, ranked by importance with sourced analysis, " +
      "plus a daily audio briefing.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#060606",
    theme_color: "#060606",
    categories: ["news", "finance"],
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
