import { Suspense } from "react";
import { headers } from "next/headers";
import "./globals.css";
import "./layout.css";
import "./bullets.css";
import "./utilities.css";
import "./audio.css";
import "./market.css";
import "./panel.css";
import "./search.css";
import "./nav.css";
import "./narrative.css";
import "./weekly.css";
import "./maps.css";
import "./entityGraph.css";
import "./entityLeague.css";
import "./threads.css";
import Header from "./components/Header";
import MarketBandeau from "./components/MarketBandeau";
import { XIcon } from "./components/icons";
import AudioProvider from "./components/AudioProvider";
import { getTvlWithChange } from "../lib/db";
import { getMarketData } from "../lib/prices";

export const dynamic = "force-dynamic";

const SITE_URL = "https://app.horyon.xyz";
const TITLE = "Horyon · Crypto Market Intelligence";
const DESCRIPTION =
  "Crypto market intelligence from 100+ sources. The day's onchain, DeFi, regulatory " +
  "and market news, ranked by importance and written up with sourced analysis. " +
  "Every figure ties back to a source.";

export const metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: TITLE,
    template: "%s · Horyon",
  },
  description: DESCRIPTION,
  applicationName: "Horyon",
  keywords: [
    "crypto intelligence", "crypto news", "DeFi", "onchain", "blockchain",
    "daily digest", "crypto market", "Horyon",
  ],
  authors: [{ name: "Horyon" }],
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true, "max-image-preview": "large", "max-snippet": -1 },
  },
  openGraph: {
    type: "website",
    siteName: "Horyon",
    title: TITLE,
    description: DESCRIPTION,
    url: SITE_URL,
    images: [{ url: "/api/og", width: 1200, height: 628, alt: "Horyon · Crypto Market Intelligence" }],
  },
  twitter: {
    card: "summary_large_image",
    site: "@Horyonhq",
    creator: "@Horyonhq",
    title: TITLE,
    description: DESCRIPTION,
    images: ["/api/og"],
  },
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/favicon-32x32.png", type: "image/png", sizes: "32x32" },
    ],
    apple: "/apple-touch-icon.png",
  },
};

// Footer market ticker — fetches CoinGecko (getMarketData) + TVL. Isolated in its own
// async component + <Suspense> so the layout (and therefore EVERY route's HTML) flushes
// immediately instead of blocking the first byte on the external CoinGecko call whenever
// its 300s data cache is cold. The ticker streams in a beat later, below the fold.
async function FooterTicker() {
  const [market, tvl] = await Promise.all([getMarketData(), getTvlWithChange()]);
  return <MarketBandeau market={market} tvl={tvl} />;
}

export default function RootLayout({ children }) {
  // Nonce minted per-request by middleware.js; stamps the inline theme script so it
  // satisfies the nonce-based CSP without needing script-src 'unsafe-inline'.
  const nonce = headers().get("x-nonce") || undefined;
  return (
    <html lang="en" data-theme="dark">
      <head>
        {/* Fonts: preconnect warms the TLS handshake to Google's font hosts before the
            stylesheet resolves, then the stylesheet downloads in parallel with our CSS.
            This replaces the old render-blocking `@import` inside globals.css (which only
            kicked off the font fetch *after* the CSS had parsed). display=swap avoids FOIT. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Raleway:wght@700;800;900&family=Cormorant:ital,wght@0,300;0,400;0,500;0,600;1,300&family=DM+Mono:ital,wght@0,300;0,400;0,500;1,300&family=Geist:wght@300;400;500;600;700&display=swap"
        />
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify({
            "@context": "https://schema.org",
            "@type": "WebSite",
            name: "Horyon",
            url: SITE_URL,
            description: DESCRIPTION,
          }) }}
        />
        {/* FOUC prevention: set theme before first paint */}
        <script nonce={nonce} dangerouslySetInnerHTML={{ __html: `
(function(){try{var t=localStorage.getItem('theme')||'dark';
document.documentElement.setAttribute('data-theme',t);}catch(e){}})();
        `.trim() }} />
      </head>
      <body>
        <AudioProvider>
        <Header />
        <main className="reader">{children}</main>
        <footer className="site-footer">
          <Suspense fallback={<div className="market-bandeau" aria-hidden="true" />}>
            <FooterTicker />
          </Suspense>
          <span className="site-footer__brand">
            <a
              href="https://x.com/Horyonhq"
              target="_blank"
              rel="noopener noreferrer"
              className="site-footer__social"
              aria-label="Horyon on X"
            >
              <XIcon size={12} />
              <span className="site-footer__social-handle">@Horyonhq</span>
            </a>
            <span className="site-footer__copy">© {new Date().getFullYear()} Horyon</span>
          </span>
        </footer>
        </AudioProvider>
      </body>
    </html>
  );
}
