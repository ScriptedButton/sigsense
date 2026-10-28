import type { NextConfig } from "next";

const transformersWeb =
  "./node_modules/@huggingface/transformers/dist/transformers.web.js";

const nextConfig: NextConfig = {
  // Lets a second dev server run beside a build without sharing .next.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  allowedDevOrigins: ["192.168.1.167"],
  // The dev "N" badge floats over the phone tab dock and swallows taps.
  devIndicators: false,
  transpilePackages: [
    "@jtarrio/webrtlsdr",
    "@jtarrio/signals",
    "@huggingface/transformers",
    "@e04/ft8ts",
    "three",
  ],
  serverExternalPackages: ["onnxruntime-node", "sharp"],
  async rewrites() {
    const apiUrl = (process.env.API_PROXY_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
    // App routes such as /api/callsign are files, so they win over this afterFiles rewrite.
    return [{ source: "/api/:path*", destination: `${apiUrl}/:path*` }];
  },
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    resolveAlias: {
      // Force the browser build — the "node" export pulls onnxruntime-node.
      "@huggingface/transformers": transformersWeb,
    },
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
