import type { NextConfig } from "next";

// Static export: `next build` writes plain files to web/out, which the
// FastAPI server (`flashtrace serve`) serves from the same origin as the API.
// That means no Node.js at run time, no CORS and no second server.
const nextConfig: NextConfig = {
  output: "export",
  // /run -> /run/index.html, so any static file server can map routes to files.
  trailingSlash: true,
  // The image optimiser needs a server; a static export cannot use it.
  images: { unoptimized: true },
  // web/ is its own npm project; stop Turbopack guessing a parent folder as root.
  turbopack: { root: __dirname },
};

export default nextConfig;
