import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the workspace root explicitly — without this Turbopack walks up
  // looking for the nearest lockfile and can land on an unrelated one
  // outside this project (see Next.js 16 root-detection warning).
  turbopack: {
    root: path.resolve(__dirname),
  },
};

export default nextConfig;
