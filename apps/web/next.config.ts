import type { NextConfig } from "next"

const nextConfig: NextConfig = {
  // "standalone" output is only for the self-hosted Docker image
  // (docker/web.Dockerfile copies .next/standalone). Vercel's build
  // pipeline does its own function bundling/tracing and doesn't need
  // it — leaving it on there makes Vercel's post-build step look for
  // the default (non-standalone) trace manifest in the wrong place.
  // `VERCEL` is set automatically in every Vercel build environment.
  output: process.env.VERCEL ? undefined : "standalone",
  turbopack: {
    // This repo has a package-lock.json both at the monorepo root and
    // here, which makes Next.js infer the monorepo root as the
    // workspace/tracing root instead of this app — pin it explicitly
    // so output-file-tracing manifests land where Vercel expects them.
    root: __dirname,
  },
}

export default nextConfig
