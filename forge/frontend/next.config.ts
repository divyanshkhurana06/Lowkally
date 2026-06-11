import type { NextConfig } from "next";

const api = process.env.API_URL || "http://127.0.0.1:8080";

export default {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
} satisfies NextConfig;
