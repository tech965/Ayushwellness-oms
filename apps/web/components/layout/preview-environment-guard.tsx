"use client"

import * as React from "react"

import { apiConfig, type ApiConfig } from "@/lib/api-config"

/** Preview builds only: says which API is in use, or blocks the whole app with
 * the reason when no safe API is configured. Production and local development
 * render the app unchanged.
 */
export function PreviewEnvironmentGuard({
  children,
  config = apiConfig,
}: {
  children: React.ReactNode
  config?: ApiConfig
}) {
  if (!config.isPreview) return <>{children}</>

  if (config.baseUrl === null) {
    return (
      <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-3 p-6">
        <div
          role="alert"
          className="space-y-3 rounded-lg border border-amber-500 bg-amber-500/10 p-5"
        >
          <h1 className="text-lg font-semibold">Preview API is not configured</h1>
          <p>{config.problem}</p>
          <p className="text-muted-foreground text-sm">
            Nothing has been sent to any API, and this preview never falls back to
            production. Set the Preview environment variable in Vercel and build the
            preview again.
          </p>
        </div>
      </main>
    )
  }

  return (
    <>
      <div
        role="status"
        data-testid="preview-banner"
        className="bg-amber-400 px-3 py-1 text-center text-xs font-semibold break-all text-black"
      >
        PREVIEW · API: {new URL(config.baseUrl).host}
      </div>
      {children}
    </>
  )
}
