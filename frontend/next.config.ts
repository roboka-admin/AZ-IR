import type { NextConfig } from "next";

/**
 * The browser only ever talks to this Next.js server; `/api/*` is proxied to the FastAPI
 * backend. That keeps AGENTS.md rule 1 (the frontend never touches the database) true even at
 * the network level, and it keeps the preview host working without CORS gymnastics.
 */
const backend = process.env.AZIR_BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Dev-server cross-origin checks: the sandbox preview is served from an *.e2b.app host.
  allowedDevOrigins: ["*.e2b.app", "localhost", "127.0.0.1"],
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/healthz", destination: `${backend}/healthz` },
      { source: "/readyz", destination: `${backend}/readyz` },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
