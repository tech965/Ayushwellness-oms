"use client"

import * as React from "react"
import Link from "next/link"
import { toast } from "sonner"

import { PageHeader } from "@/components/shared/page-header"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { QueryStates } from "@/components/shared/query-states"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDateTime } from "@/lib/format"
import { useCouriers } from "@/services/couriers"
import { useIntegrations } from "@/services/integrations"
import { useSettings, useUpdateSettings } from "@/services/settings"
import type {
  AppearanceSettings,
  AppSettingsResponse,
  DashboardSettings,
  GeneralSettings,
  NotificationSettings,
  OrderSettings,
  SecuritySettings,
  ShippingSettings,
} from "@/types/settings"

export default function SettingsPage() {
  const query = useSettings()

  return (
    <>
      <PageHeader
        title="Settings"
        description="Organization-wide OMS configuration — every change here applies immediately for every user."
      />
      {/* Was previously `isLoading || !data ? <Skeleton> : <SettingsView>` --
       * once a request permanently fails (e.g. a 500), `isLoading` settles
       * to `false` but `data` never arrives, so that condition stayed true
       * forever: an infinite skeleton with no error message and no way to
       * retry. `QueryStates` (the same loading/error/empty handling every
       * other page already uses) shows the real, sanitized error message
       * plus a manual Retry button instead -- never a raw stack trace,
       * never a silent hang, never an automatic retry loop.
       */}
      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => void query.refetch()}
        emptyTitle="Settings unavailable"
        emptyDescription="Could not load settings right now. The rest of the OMS is unaffected."
      >
        {(data) => <SettingsView data={data} />}
      </QueryStates>
    </>
  )
}

function SettingsView({ data }: { data: AppSettingsResponse }) {
  return (
    <div className="flex flex-col gap-4">
      {data.updated_at ? (
        <p className="text-muted-foreground text-xs">
          Last updated {formatDateTime(data.updated_at)}
          {data.updated_by_email ? ` by ${data.updated_by_email}` : ""}
        </p>
      ) : null}
      <Tabs defaultValue="general">
        <TabsList className="flex-wrap">
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="orders">Orders</TabsTrigger>
          <TabsTrigger value="notifications">Notifications</TabsTrigger>
          <TabsTrigger value="shipping">Shipping</TabsTrigger>
          <TabsTrigger value="dashboard">Dashboard</TabsTrigger>
          <TabsTrigger value="security">Security</TabsTrigger>
          <TabsTrigger value="integrations">Integrations</TabsTrigger>
          <TabsTrigger value="appearance">Appearance</TabsTrigger>
        </TabsList>
        <TabsContent value="general">
          <GeneralSection initial={data.settings.general} />
        </TabsContent>
        <TabsContent value="orders">
          <OrdersSection initial={data.settings.orders} />
        </TabsContent>
        <TabsContent value="notifications">
          <NotificationsSection initial={data.settings.notifications} />
        </TabsContent>
        <TabsContent value="shipping">
          <ShippingSection initial={data.settings.shipping} />
        </TabsContent>
        <TabsContent value="dashboard">
          <DashboardSection initial={data.settings.dashboard} />
        </TabsContent>
        <TabsContent value="security">
          <SecuritySection initial={data.settings.security} />
        </TabsContent>
        <TabsContent value="integrations">
          <IntegrationsSection />
        </TabsContent>
        <TabsContent value="appearance">
          <AppearanceSection initial={data.settings.appearance} />
        </TabsContent>
      </Tabs>
    </div>
  )
}

function SectionCard({
  title,
  description,
  children,
  onSave,
  isSaving,
}: {
  title: string
  description: string
  children: React.ReactNode
  onSave: () => void
  isSaving: boolean
}) {
  // Every field above stays interactive either way (so a non-manager can
  // still see what would change), but only `settings.manage` can persist
  // it -- matches the PUT endpoint's own RBAC gate, and avoids a
  // confusing 403 toast on Save for everyone else.
  const { hasPermission } = useAuth()
  const canManage = hasPermission("settings.manage")

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 sm:grid-cols-2">{children}</CardContent>
      <CardFooter className="items-center justify-end gap-3">
        {!canManage && (
          <p className="text-muted-foreground text-xs">
            You don&apos;t have permission to change settings.
          </p>
        )}
        <Button onClick={onSave} disabled={isSaving || !canManage}>
          {isSaving ? "Saving..." : "Save"}
        </Button>
      </CardFooter>
    </Card>
  )
}

function useSaveSection<K extends keyof AppSettingsResponse["settings"]>(section: K) {
  const mutation = useUpdateSettings()
  const save = (value: AppSettingsResponse["settings"][K]) => {
    mutation.mutate(
      { [section]: value } as Partial<AppSettingsResponse["settings"]>,
      {
        onSuccess: () => toast.success("Settings saved."),
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }
  return { save, isSaving: mutation.isPending }
}

function GeneralSection({ initial }: { initial: GeneralSettings }) {
  const [value, setValue] = React.useState(initial)
  const { save, isSaving } = useSaveSection("general")

  return (
    <SectionCard
      title="General"
      description="Organization identity and display defaults used across the OMS."
      onSave={() => save(value)}
      isSaving={isSaving}
    >
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="org-name">Organization name</Label>
        <Input
          id="org-name"
          value={value.organization_name}
          onChange={(e) => setValue({ ...value, organization_name: e.target.value })}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="oms-name">OMS display name</Label>
        <Input
          id="oms-name"
          value={value.oms_display_name}
          onChange={(e) => setValue({ ...value, oms_display_name: e.target.value })}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>Default timezone</Label>
        <Select value={value.default_timezone} disabled>
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="Asia/Kolkata">Asia/Kolkata (IST)</SelectItem>
          </SelectContent>
        </Select>
        <p className="text-muted-foreground text-xs">
          Every date-scoped feature in this OMS is IST-anchored; this is informational.
        </p>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="currency">Currency</Label>
        <Input
          id="currency"
          value={value.currency}
          onChange={(e) => setValue({ ...value, currency: e.target.value.toUpperCase() })}
          maxLength={3}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>Date format</Label>
        <Select
          value={value.date_format}
          onValueChange={(v) =>
            setValue({ ...value, date_format: v as GeneralSettings["date_format"] })
          }
        >
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="DD MMM YYYY">DD MMM YYYY (25 Aug 2026)</SelectItem>
            <SelectItem value="DD/MM/YYYY">DD/MM/YYYY</SelectItem>
            <SelectItem value="MM/DD/YYYY">MM/DD/YYYY</SelectItem>
            <SelectItem value="YYYY-MM-DD">YYYY-MM-DD</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="page-size">Default pagination size</Label>
        <Input
          id="page-size"
          type="number"
          min={5}
          max={200}
          value={value.default_page_size}
          onChange={(e) =>
            setValue({ ...value, default_page_size: Number(e.target.value) || 20 })
          }
        />
        <p className="text-muted-foreground text-xs">
          Applied to every list page that doesn&apos;t already have its own page-size control.
        </p>
      </div>
    </SectionCard>
  )
}

function OrdersSection({ initial }: { initial: OrderSettings }) {
  const [value, setValue] = React.useState(initial)
  const { save, isSaving } = useSaveSection("orders")

  return (
    <SectionCard
      title="Orders"
      description="Defaults for new orders and the Orders list."
      onSave={() => save(value)}
      isSaving={isSaving}
    >
      <div className="flex flex-col gap-1.5">
        <Label>Default order status</Label>
        <Select
          value={value.default_order_status}
          onValueChange={(v) =>
            setValue({ ...value, default_order_status: v as OrderSettings["default_order_status"] })
          }
        >
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="pending">Pending</SelectItem>
            <SelectItem value="confirmed">Confirmed</SelectItem>
            <SelectItem value="processing">Processing</SelectItem>
            <SelectItem value="packed">Packed</SelectItem>
            <SelectItem value="shipped">Shipped</SelectItem>
            <SelectItem value="delivered">Delivered</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="order-refresh">Auto-refresh interval (seconds)</Label>
        <Input
          id="order-refresh"
          type="number"
          min={0}
          max={3600}
          value={value.auto_refresh_interval_seconds}
          onChange={(e) =>
            setValue({ ...value, auto_refresh_interval_seconds: Number(e.target.value) || 0 })
          }
        />
        <p className="text-muted-foreground text-xs">0 disables auto-refresh.</p>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>Default sort field</Label>
        <Select
          value={value.default_sort_field}
          onValueChange={(v) =>
            setValue({ ...value, default_sort_field: v as OrderSettings["default_sort_field"] })
          }
        >
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="order_datetime">Order date</SelectItem>
            <SelectItem value="total_amount">Order total</SelectItem>
            <SelectItem value="order_number">Order number</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>Default sort direction</Label>
        <Select
          value={value.default_sort_direction}
          onValueChange={(v) =>
            setValue({
              ...value,
              default_sort_direction: v as OrderSettings["default_sort_direction"],
            })
          }
        >
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="desc">Newest first</SelectItem>
            <SelectItem value="asc">Oldest first</SelectItem>
          </SelectContent>
        </Select>
      </div>
    </SectionCard>
  )
}

function NotificationToggle({
  id,
  label,
  checked,
  onCheckedChange,
}: {
  id: string
  label: string
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}) {
  return (
    <div className="flex items-center gap-2">
      <Checkbox
        id={id}
        checked={checked}
        onCheckedChange={(c) => onCheckedChange(c === true)}
      />
      <Label htmlFor={id} className="font-normal">
        {label}
      </Label>
    </div>
  )
}

function NotificationsSection({ initial }: { initial: NotificationSettings }) {
  const [value, setValue] = React.useState(initial)
  const { save, isSaving } = useSaveSection("notifications")

  return (
    <SectionCard
      title="Notifications"
      description="Email notification preferences."
      onSave={() => save(value)}
      isSaving={isSaving}
    >
      <NotificationToggle
        id="notif-orders"
        label="Order notifications"
        checked={value.email_order_notifications}
        onCheckedChange={(c) => setValue({ ...value, email_order_notifications: c })}
      />
      <NotificationToggle
        id="notif-shipments"
        label="Shipment notifications"
        checked={value.email_shipment_notifications}
        onCheckedChange={(c) => setValue({ ...value, email_shipment_notifications: c })}
      />
      <NotificationToggle
        id="notif-returns"
        label="Return/refund notifications"
        checked={value.email_return_refund_notifications}
        onCheckedChange={(c) => setValue({ ...value, email_return_refund_notifications: c })}
      />
    </SectionCard>
  )
}

function ShippingSection({ initial }: { initial: ShippingSettings }) {
  const [value, setValue] = React.useState(initial)
  const { save, isSaving } = useSaveSection("shipping")
  const couriersQuery = useCouriers()

  return (
    <SectionCard
      title="Shipping"
      description="Courier and tracking preferences."
      onSave={() => save(value)}
      isSaving={isSaving}
    >
      <div className="flex flex-col gap-1.5">
        <Label>Default courier</Label>
        <Select
          value={value.default_courier_id ?? "none"}
          onValueChange={(v) => setValue({ ...value, default_courier_id: v === "none" ? null : v })}
        >
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">No default</SelectItem>
            {(couriersQuery.data ?? []).map((courier) => (
              <SelectItem key={courier.id} value={courier.id}>
                {courier.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="tracking-refresh">Tracking refresh interval (minutes)</Label>
        <Input
          id="tracking-refresh"
          type="number"
          min={5}
          max={1440}
          value={value.tracking_refresh_interval_minutes}
          onChange={(e) =>
            setValue({
              ...value,
              tracking_refresh_interval_minutes: Number(e.target.value) || 60,
            })
          }
        />
      </div>
    </SectionCard>
  )
}

function DashboardSection({ initial }: { initial: DashboardSettings }) {
  const [value, setValue] = React.useState(initial)
  const { save, isSaving } = useSaveSection("dashboard")

  return (
    <SectionCard
      title="Dashboard"
      description="Defaults for the main Dashboard page."
      onSave={() => save(value)}
      isSaving={isSaving}
    >
      <div className="flex flex-col gap-1.5">
        <Label>Default date range</Label>
        <Select
          value={value.default_date_range}
          onValueChange={(v) =>
            setValue({ ...value, default_date_range: v as DashboardSettings["default_date_range"] })
          }
        >
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="today">Today</SelectItem>
            <SelectItem value="yesterday">Yesterday</SelectItem>
            <SelectItem value="this_week">This Week</SelectItem>
            <SelectItem value="last_7_days">Last 7 Days</SelectItem>
            <SelectItem value="last_30_days">Last 30 Days</SelectItem>
            <SelectItem value="this_month">This Month</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>Default chart interval</Label>
        <Select
          value={value.default_chart_interval}
          onValueChange={(v) =>
            setValue({
              ...value,
              default_chart_interval: v as DashboardSettings["default_chart_interval"],
            })
          }
        >
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="day">Daily</SelectItem>
            <SelectItem value="week">Weekly</SelectItem>
            <SelectItem value="month">Monthly</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="dash-refresh">Dashboard refresh interval (seconds)</Label>
        <Input
          id="dash-refresh"
          type="number"
          min={0}
          max={3600}
          value={value.refresh_interval_seconds}
          onChange={(e) =>
            setValue({ ...value, refresh_interval_seconds: Number(e.target.value) || 0 })
          }
        />
        <p className="text-muted-foreground text-xs">0 disables auto-refresh.</p>
      </div>
    </SectionCard>
  )
}

function SecuritySection({ initial }: { initial: SecuritySettings }) {
  const [value, setValue] = React.useState(initial)
  const { save, isSaving } = useSaveSection("security")

  return (
    <SectionCard
      title="Security"
      description="Session behavior for every signed-in user."
      onSave={() => save(value)}
      isSaving={isSaving}
    >
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="session-timeout">Session timeout (minutes)</Label>
        <Input
          id="session-timeout"
          type="number"
          min={0}
          max={1440}
          value={value.session_timeout_minutes}
          onChange={(e) =>
            setValue({ ...value, session_timeout_minutes: Number(e.target.value) || 0 })
          }
        />
        <p className="text-muted-foreground text-xs">
          Signs out an idle user after this many minutes of no activity. 0 disables it.
        </p>
      </div>
    </SectionCard>
  )
}

function AppearanceSection({ initial }: { initial: AppearanceSettings }) {
  const [value, setValue] = React.useState(initial)
  const { save, isSaving } = useSaveSection("appearance")

  return (
    <SectionCard
      title="Appearance"
      description="Table density and layout preferences."
      onSave={() => save(value)}
      isSaving={isSaving}
    >
      <div className="flex flex-col gap-1.5">
        <Label>Table density</Label>
        <Select
          value={value.table_density}
          onValueChange={(v) =>
            setValue({ ...value, table_density: v as AppearanceSettings["table_density"] })
          }
        >
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="comfortable">Comfortable</SelectItem>
            <SelectItem value="compact">Compact</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <p className="text-muted-foreground text-xs sm:col-span-2">
        Table page size is controlled by General -&gt; Default pagination size.
      </p>
    </SectionCard>
  )
}

function IntegrationStatusRow({
  name,
  status,
  lastSyncAt,
}: {
  name: string
  status: string | undefined
  lastSyncAt: string | null | undefined
}) {
  const isConnected = status === "connected"
  return (
    <div className="flex items-center justify-between border-b py-3 last:border-b-0">
      <div>
        <p className="font-medium">{name}</p>
        <p className="text-muted-foreground text-xs">
          {lastSyncAt ? `Last sync: ${formatDateTime(lastSyncAt)}` : "Never synced"}
        </p>
      </div>
      <Badge variant={isConnected ? "default" : "outline"}>
        {status ? status[0].toUpperCase() + status.slice(1) : "Unknown"}
      </Badge>
    </div>
  )
}

function IntegrationsSection() {
  const integrationsQuery = useIntegrations({ page: 1, pageSize: 50 })
  const integrations = integrationsQuery.data?.data ?? []
  const shopify = integrations.find((i) => i.code === "shopify")
  const shiprocket = integrations.find((i) => i.code === "shiprocket")

  return (
    <Card>
      <CardHeader>
        <CardTitle>Integrations</CardTitle>
        <CardDescription>
          Live connection status — see{" "}
          <Link href="/integrations" className="text-primary hover:underline">
            Integrations
          </Link>{" "}
          for sync history and manual actions. No credentials are shown here.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {integrationsQuery.isLoading ? (
          <div className="bg-muted h-24 w-full animate-pulse rounded-md" />
        ) : (
          <>
            <IntegrationStatusRow
              name="Shopify"
              status={shopify?.status}
              lastSyncAt={shopify?.last_successful_sync_at}
            />
            <IntegrationStatusRow
              name="Shiprocket"
              status={shiprocket?.status}
              lastSyncAt={shiprocket?.last_successful_sync_at}
            />
          </>
        )}
      </CardContent>
    </Card>
  )
}
