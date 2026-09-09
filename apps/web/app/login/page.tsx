"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"

import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { getApiErrorMessage, getStoredAccessToken } from "@/lib/api-client"
import { loginSchema, type LoginInput } from "@/lib/validation/auth"
import { login } from "@/services/auth"

export default function LoginPage() {
  const router = useRouter()
  const queryClient = useQueryClient()

  React.useEffect(() => {
    if (getStoredAccessToken()) {
      router.replace("/dashboard")
    }
  }, [router])

  const form = useForm<LoginInput>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  })

  const mutation = useMutation({
    mutationFn: login,
    // Root cause of a previous user's role/nav briefly (or persistently,
    // within the `["auth", "me"]` query's `staleTime`) rendering after a
    // DIFFERENT user logs in in the same tab: the `QueryClient` is a
    // single long-lived instance for the whole app, and this is the one
    // place a fresh login re-enables that query -- without clearing it
    // first, React Query happily serves the previous session's cached
    // response (Admin's roles/permissions, an old user's order list,
    // etc.) until it naturally goes stale. `logout()` (`lib/auth-
    // context.tsx`) already does this same `queryClient.clear()`; this
    // mirrors it on the other side of the same handoff so neither
    // direction can leak stale cross-user data.
    onSuccess: () => {
      queryClient.clear()
      router.push("/dashboard")
    },
  })

  function onSubmit(values: LoginInput) {
    mutation.mutate(values)
  }

  return (
    <div className="bg-muted/30 flex min-h-dvh items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <div className="bg-primary text-primary-foreground mb-1 flex size-9 items-center justify-center rounded-md text-sm font-semibold">
            A
          </div>
          <CardTitle>Sign in to AyushWellness OMS</CardTitle>
          <CardDescription>Operations Intelligence Platform</CardDescription>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="flex flex-col gap-4">
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Email</FormLabel>
                    <FormControl>
                      <Input
                        type="email"
                        autoComplete="email"
                        placeholder="you@ayushwellness.com"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Password</FormLabel>
                    <FormControl>
                      <Input type="password" autoComplete="current-password" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {mutation.isError && (
                <p className="text-destructive text-sm" role="alert">
                  {getApiErrorMessage(mutation.error)}
                </p>
              )}

              <Button type="submit" className="mt-1 w-full" disabled={mutation.isPending}>
                {mutation.isPending ? "Signing in..." : "Sign in"}
              </Button>
            </form>
          </Form>
        </CardContent>
      </Card>
    </div>
  )
}
