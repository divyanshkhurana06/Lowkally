import type { NextConfig } from "next";

const raw = process.env.API_URL || "http://127.0.0.1:8080";
const api =
  raw.startsWith("http://") || raw.startsWith("https://")
    ? raw.replace(/\/$/, "")
    : `https://${raw.replace(/\/$/, "")}`;

export default {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
} satisfies NextConfig;
