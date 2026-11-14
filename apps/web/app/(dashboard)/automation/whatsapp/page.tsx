"use client"

import * as React from "react"
import { Download } from "lucide-react"
import { toast } from "sonner"

import { PageHeader } from "@/components/shared/page-header"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDateTime } from "@/lib/format"
import {
  downloadQueueExport,
  useCancelQueuedMessage,
  useContacts,
  useCreateContact,
  useCreateGroup,
  useCreateSchedule,
  useDeleteSchedule,
  useGroups,
  useQueueHistory,
  useSchedules,
  useSetContactOptStatus,
  useUpdateSchedule,
  useWhatsAppDashboard,
  useWhatsAppStatus,
} from "@/services/whatsapp-automation"
import type { MessageType, QueueStatus, RecipientSpec, ScheduleKind } from "@/types/whatsapp-automation"

const QUEUE_STATUSES: QueueStatus[] = [
  "QUEUED",
  "SENDING",
  "SENT",
  "DELIVERED",
  "READ",
  "FAILED",
  "CANCELLED",
]

function NotConfiguredAlert({ missing }: { missing: string[] }) {
  return (
    <Alert className="mb-4" variant="destructive">
      <AlertTitle>WhatsApp is not configured</AlertTitle>
      <AlertDescription>
        Missing environment variables: {missing.join(", ")}. Schedules/contacts can still be
        created, but nothing will actually send until these are set.
      </AlertDescription>
    </Alert>
  )
}

function DashboardTab() {
  const { data, isLoading } = useWhatsAppDashboard()
  if (isLoading) return <Skeleton className="h-40 w-full" />
  if (!data) return null
  const cards: [string, string | number][] = [
    ["Active schedules", data.active_schedules],
    ["Paused schedules", data.paused_schedules],
    ["Pending messages", data.pending_messages],
    ["Sent today", data.sent_today],
    ["Delivered today", data.delivered_today],
    ["Failed today", data.failed_today],
    ["Next report run", data.next_report_run_utc],
    ["Latest report status", data.latest_daily_report_status],
  ]
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {cards.map(([label, value]) => (
        <Card key={label}>
          <CardHeader>
            <CardTitle className="text-muted-foreground text-xs font-normal">{label}</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-semibold">{value}</CardContent>
        </Card>
      ))}
    </div>
  )
}

function ContactsTab() {
  const contacts = useContacts()
  const groups = useGroups()
  const createContact = useCreateContact()
  const setOptStatus = useSetContactOptStatus()
  const createGroup = useCreateGroup()
  const [name, setName] = React.useState("")
  const [phone, setPhone] = React.useState("")
  const [groupName, setGroupName] = React.useState("")
  const [groupMembers, setGroupMembers] = React.useState<Set<string>>(new Set())

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Add contact</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            Name
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="text-sm">
            Phone
            <Input
              placeholder="9991234567"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
            />
          </label>
          <Button
            disabled={!name || !phone || createContact.isPending}
            onClick={() =>
              createContact.mutate(
                { name, phone },
                {
                  onSuccess: () => {
                    setName("")
                    setPhone("")
                  },
                  onError: (e) => toast.error(getApiErrorMessage(e)),
                }
              )
            }
          >
            Add
          </Button>
        </CardContent>
      </Card>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Phone</TableHead>
            <TableHead>Opt-in status</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {(contacts.data ?? []).map((c) => (
            <TableRow key={c.id}>
              <TableCell>{c.name}</TableCell>
              <TableCell>{c.phone_number}</TableCell>
              <TableCell>
                <Badge variant={c.opted_in ? "default" : "outline"}>
                  {c.opted_in ? "Opted in" : "Opted out"}
                </Badge>
              </TableCell>
              <TableCell>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    setOptStatus.mutate({ id: c.id, optedIn: !c.opted_in })
                  }
                >
                  {c.opted_in ? "Opt out" : "Opt in"}
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Create recipient group</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Input
            placeholder="Group name (e.g. Management)"
            value={groupName}
            onChange={(e) => setGroupName(e.target.value)}
          />
          <div className="flex flex-wrap gap-3">
            {(contacts.data ?? []).map((c) => (
              <label key={c.id} className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={groupMembers.has(c.id)}
                  onCheckedChange={(checked) => {
                    setGroupMembers((prev) => {
                      const next = new Set(prev)
                      if (checked) next.add(c.id)
                      else next.delete(c.id)
                      return next
                    })
                  }}
                />
                {c.name}
              </label>
            ))}
          </div>
          <Button
            disabled={!groupName || createGroup.isPending}
            onClick={() =>
              createGroup.mutate(
                { name: groupName, contact_ids: [...groupMembers] },
                {
                  onSuccess: () => {
                    setGroupName("")
                    setGroupMembers(new Set())
                  },
                  onError: (e) => toast.error(getApiErrorMessage(e)),
                }
              )
            }
          >
            Create group
          </Button>
        </CardContent>
      </Card>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Group</TableHead>
            <TableHead className="text-right">Members</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(groups.data ?? []).map((g) => (
            <TableRow key={g.id}>
              <TableCell>{g.name}</TableCell>
              <TableCell className="text-right">{g.member_count}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function ScheduleForm() {
  const contacts = useContacts()
  const groups = useGroups()
  const createSchedule = useCreateSchedule()
  const [name, setName] = React.useState("")
  const [kind, setKind] = React.useState<ScheduleKind>("one_time")
  const [messageType, setMessageType] = React.useState<MessageType>("text")
  const [bodyText, setBodyText] = React.useState("")
  const [templateName, setTemplateName] = React.useState("")
  const [templateLanguage, setTemplateLanguage] = React.useState("en")
  const [sendAt, setSendAt] = React.useState("")
  const [timeOfDay, setTimeOfDay] = React.useState("09:00")
  const [recipients, setRecipients] = React.useState<Set<string>>(new Set())

  const toggleRecipient = (key: string) =>
    setRecipients((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const submit = () => {
    const recipientSpecs: RecipientSpec[] = [...recipients].map((key) => {
      const [type, id] = key.split(":")
      return type === "contact" ? { contact_id: id } : { group_id: id }
    })
    createSchedule.mutate(
      {
        name,
        schedule_kind: kind,
        message_type: messageType,
        body_text: messageType === "text" ? bodyText : undefined,
        template_name: messageType === "template" ? templateName : undefined,
        template_language: messageType === "template" ? templateLanguage : undefined,
        send_at: kind === "one_time" ? new Date(sendAt).toISOString() : undefined,
        time_of_day: kind !== "one_time" ? timeOfDay : undefined,
        recipients: recipientSpecs,
      },
      {
        onSuccess: () => {
          setName("")
          setBodyText("")
          setRecipients(new Set())
        },
        onError: (e) => toast.error(getApiErrorMessage(e)),
      }
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">New schedule</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Input placeholder="Schedule name" value={name} onChange={(e) => setName(e.target.value)} />
        <div className="flex flex-wrap gap-3">
          <label className="text-sm">
            Type
            <Select value={kind} onValueChange={(v) => setKind(v as ScheduleKind)}>
              <SelectTrigger className="w-[220px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="one_time">One-time</SelectItem>
                <SelectItem value="recurring_daily">Recurring (daily)</SelectItem>
                <SelectItem value="daily_sales_report">Daily sales report (9 AM IST)</SelectItem>
              </SelectContent>
            </Select>
          </label>
          <label className="text-sm">
            Message type
            <Select value={messageType} onValueChange={(v) => setMessageType(v as MessageType)}>
              <SelectTrigger className="w-[160px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="text">Text</SelectItem>
                <SelectItem value="template">Template</SelectItem>
                <SelectItem value="image">Image</SelectItem>
              </SelectContent>
            </Select>
          </label>
          {kind === "one_time" ? (
            <label className="text-sm">
              Send at
              <Input
                type="datetime-local"
                value={sendAt}
                onChange={(e) => setSendAt(e.target.value)}
              />
            </label>
          ) : (
            <label className="text-sm">
              Time of day (Asia/Kolkata)
              <Input
                type="time"
                value={timeOfDay}
                onChange={(e) => setTimeOfDay(e.target.value)}
              />
            </label>
          )}
        </div>
        {messageType === "text" && (
          <Textarea
            placeholder="Message body"
            value={bodyText}
            onChange={(e) => setBodyText(e.target.value)}
          />
        )}
        {messageType === "template" && (
          <div className="flex flex-wrap gap-3">
            <Input
              placeholder="Approved template name"
              value={templateName}
              onChange={(e) => setTemplateName(e.target.value)}
            />
            <Input
              placeholder="Language code (e.g. en_US)"
              value={templateLanguage}
              onChange={(e) => setTemplateLanguage(e.target.value)}
            />
          </div>
        )}
        {kind === "daily_sales_report" && (
          <Alert>
            <AlertTitle>Daily sales report note</AlertTitle>
            <AlertDescription>
              If message type is &quot;template&quot;, the report text is sent as the first body
              parameter -- the template must have a body placeholder that fits a long message, or
              be an approved media template once a report image is available.
            </AlertDescription>
          </Alert>
        )}
        <div>
          <p className="mb-1 text-sm font-medium">Recipients</p>
          <div className="flex flex-wrap gap-3">
            {(contacts.data ?? []).map((c) => (
              <label key={c.id} className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={recipients.has(`contact:${c.id}`)}
                  onCheckedChange={() => toggleRecipient(`contact:${c.id}`)}
                />
                {c.name}
              </label>
            ))}
            {(groups.data ?? []).map((g) => (
              <label key={g.id} className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={recipients.has(`group:${g.id}`)}
                  onCheckedChange={() => toggleRecipient(`group:${g.id}`)}
                />
                {g.name} (group)
              </label>
            ))}
          </div>
        </div>
        <Button
          disabled={!name || recipients.size === 0 || createSchedule.isPending}
          onClick={submit}
        >
          Create schedule
        </Button>
      </CardContent>
    </Card>
  )
}

function SchedulesTab() {
  const schedules = useSchedules()
  const updateSchedule = useUpdateSchedule()
  const deleteSchedule = useDeleteSchedule()

  return (
    <div className="space-y-6">
      <ScheduleForm />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Kind</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Recipients</TableHead>
            <TableHead>Status</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {(schedules.data ?? []).length === 0 && (
            <TableRow>
              <TableCell colSpan={6} className="text-muted-foreground text-center">
                No schedules yet.
              </TableCell>
            </TableRow>
          )}
          {(schedules.data ?? []).map((s) => (
            <TableRow key={s.id}>
              <TableCell>{s.name}</TableCell>
              <TableCell>{s.schedule_kind}</TableCell>
              <TableCell>{s.message_type}</TableCell>
              <TableCell>{s.recipient_count}</TableCell>
              <TableCell>
                <Badge variant={!s.is_enabled ? "outline" : s.is_paused ? "secondary" : "default"}>
                  {!s.is_enabled ? "OFF" : s.is_paused ? "Paused" : "Active"}
                </Badge>
              </TableCell>
              <TableCell className="space-x-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    updateSchedule.mutate({ id: s.id, body: { is_enabled: !s.is_enabled } })
                  }
                >
                  {s.is_enabled ? "Turn off" : "Turn on"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!s.is_enabled}
                  onClick={() =>
                    updateSchedule.mutate({ id: s.id, body: { is_paused: !s.is_paused } })
                  }
                >
                  {s.is_paused ? "Resume" : "Pause"}
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => {
                    if (confirm(`Delete schedule "${s.name}"?`)) {
                      deleteSchedule.mutate(s.id, {
                        onError: (e) => toast.error(getApiErrorMessage(e)),
                      })
                    }
                  }}
                >
                  Delete
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function HistoryTab() {
  const [status, setStatus] = React.useState("__all__")
  const cancelMessage = useCancelQueuedMessage()
  const filters = status === "__all__" ? {} : { status }
  const queue = useQueueHistory(filters)
  const [exporting, setExporting] = React.useState(false)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">
          Status
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">All</SelectItem>
              {QUEUE_STATUSES.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <Button
          variant="outline"
          disabled={exporting}
          onClick={async () => {
            setExporting(true)
            try {
              await downloadQueueExport(filters)
            } catch (e) {
              toast.error(getApiErrorMessage(e))
            } finally {
              setExporting(false)
            }
          }}
        >
          <Download className="mr-2 size-4" /> {exporting ? "Exporting…" : "Export Excel"}
        </Button>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Recipient</TableHead>
            <TableHead>Schedule</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Scheduled at</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Retries</TableHead>
            <TableHead>Failure reason</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {(queue.data ?? []).length === 0 && (
            <TableRow>
              <TableCell colSpan={8} className="text-muted-foreground text-center">
                No messages match.
              </TableCell>
            </TableRow>
          )}
          {(queue.data ?? []).map((m) => (
            <TableRow key={m.id}>
              <TableCell>
                {m.contact_name} ({m.contact_phone})
              </TableCell>
              <TableCell>{m.schedule_name ?? "—"}</TableCell>
              <TableCell>{m.message_type}</TableCell>
              <TableCell>{formatDateTime(m.scheduled_send_at)}</TableCell>
              <TableCell>
                <Badge variant={m.status === "FAILED" ? "destructive" : "outline"}>
                  {m.status}
                </Badge>
              </TableCell>
              <TableCell>{m.retry_count}</TableCell>
              <TableCell className="max-w-xs truncate">{m.failure_reason ?? ""}</TableCell>
              <TableCell>
                {m.status === "QUEUED" && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      cancelMessage.mutate(m.id, {
                        onError: (e) => toast.error(getApiErrorMessage(e)),
                      })
                    }
                  >
                    Cancel
                  </Button>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

export default function WhatsAppAutomationPage() {
  const status = useWhatsAppStatus()
  return (
    <>
      <PageHeader
        title="WhatsApp Automation"
        description="Scheduled messaging and the automated daily sales report. Live-sends only -- nothing fabricated."
      />
      {status.data && !status.data.configured && (
        <NotConfiguredAlert missing={status.data.missing} />
      )}
      <Tabs defaultValue="dashboard">
        <TabsList>
          <TabsTrigger value="dashboard">Dashboard</TabsTrigger>
          <TabsTrigger value="schedules">Schedules</TabsTrigger>
          <TabsTrigger value="contacts">Contacts &amp; Groups</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
        </TabsList>
        <TabsContent value="dashboard">
          <DashboardTab />
        </TabsContent>
        <TabsContent value="schedules">
          <SchedulesTab />
        </TabsContent>
        <TabsContent value="contacts">
          <ContactsTab />
        </TabsContent>
        <TabsContent value="history">
          <HistoryTab />
        </TabsContent>
      </Tabs>
    </>
  )
}
