import type { NextConfig } from "next"

const nextConfig: NextConfig = {
  // Tells the browser bundle whether this is a Vercel preview, so it can refuse
  // to use the production API (lib/api-config.ts). VERCEL_ENV is set by Vercel
  // ("production" | "preview" | "development") and always wins; the manual value
  // is only for builds where it is absent, e.g. rehearsing a preview locally.
  env: {
    NEXT_PUBLIC_DEPLOY_ENV:
      process.env.VERCEL_ENV ?? process.env.NEXT_PUBLIC_DEPLOY_ENV ?? "",
  },
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
