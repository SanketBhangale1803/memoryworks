import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  async redirects() {
    return [
      {
        // OAuth callbacks and session cookies use localhost during local
        // development. Keep that canonical origin without deploying a runtime
        // middleware function for this host-only redirect.
        source: "/:path*",
        has: [{ type: "host", value: "127.0.0.1" }],
        destination: "http://localhost:3000/:path*",
        permanent: false,
      },
      // Pages folded into the one chat, or retired with the product's
      // runbook and simulation era. Old links land somewhere useful.
      ...["/webmcp", "/ask", "/simulation", "/benchmarks", "/updates/:path*", "/runbooks/:path*"].map(
        (source) => ({ source, destination: "/workspace", permanent: false }),
      ),
      ...["/drift", "/reliability/:path*", "/admin"].map((source) => ({
        source,
        destination: "/approvals",
        permanent: false,
      })),
      { source: "/updates", destination: "/workspace", permanent: false },
      { source: "/runbooks", destination: "/workspace", permanent: false },
      { source: "/reliability", destination: "/approvals", permanent: false },
    ];
  },
};

export default nextConfig;
