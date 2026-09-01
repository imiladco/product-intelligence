import type { NextConfig } from "next";

const INTERNAL_API_BASE_URL =
  process.env.INTERNAL_API_BASE_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // Standalone output keeps the production image small (Milestone 7).
  output: "standalone",
  // In development the browser talks to Next.js on :3000 and Django on :8000.
  // Proxying /api and /admin makes development same-origin, exactly like
  // production behind the reverse proxy — so cookies and CSRF behave the same
  // in both, and nothing works only in development.
  //
  // In production the reverse proxy owns these paths and Next.js never sees
  // them, so the rewrites are development-only.
  async rewrites() {
    if (process.env.NODE_ENV === "production") {
      return [];
    }
    // Only /api. Django admin is reached directly on the API port in
    // development (http://127.0.0.1:8000/admin/) and through the reverse proxy
    // in production; routing it through Next.js would reintroduce the
    // trailing-slash normalization that API paths avoid by being slashless.
    return [
      { source: "/api/:path*", destination: `${INTERNAL_API_BASE_URL}/api/:path*` },
    ];
  },
};

export default nextConfig;
