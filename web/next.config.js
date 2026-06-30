/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // Drop the `X-Powered-By: Next.js` header from every response — one fewer header byte on
  // the wire per request and one fewer framework/version fingerprint for opportunistic probes.
  poweredByHeader: false,
  // Lint is enforced by the dedicated `web-lint` CI job (`npm run lint`), NOT during the
  // production build. Keeping it out of `next build` means a lint finding can never break the
  // Docker image / a deploy — the digest pipeline must keep shipping even with a lint nit.
  eslint: { ignoreDuringBuilds: true },
};

module.exports = nextConfig;
