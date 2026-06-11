import "./globals.css";
import Header from "./components/Header";
import { getGovernanceProposals } from "../lib/db";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Horyon · Crypto Intelligence Feed",
  description: "Daily crypto-intelligence digests — Horyon",
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/favicon-32x32.png", type: "image/png", sizes: "32x32" },
    ],
    apple: "/apple-touch-icon.png",
  },
};

export default async function RootLayout({ children }) {
  let governance = [];
  try {
    governance = await getGovernanceProposals();
  } catch {
    // DB not reachable yet — render an empty shell rather than crashing.
  }

  return (
    <html lang="en" data-theme="dark">
      <head>
        {/* FOUC prevention: set theme before first paint */}
        <script dangerouslySetInnerHTML={{ __html: `
(function(){try{var t=localStorage.getItem('theme')||
(window.matchMedia('(prefers-color-scheme:light)').matches?'light':'dark');
document.documentElement.setAttribute('data-theme',t);}catch(e){}})();
        `.trim() }} />
      </head>
      <body>
        <Header governance={governance} />
        <main className="reader">{children}</main>
      </body>
    </html>
  );
}
