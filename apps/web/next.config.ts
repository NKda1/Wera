import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The browser only ever talks to WERA's own API routes (ARCHITECTURE §7.2 #11).
  poweredByHeader: false,
  devIndicators: { position: "bottom-right" },
};

export default nextConfig;
