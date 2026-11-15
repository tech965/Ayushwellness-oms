"use client"

import * as React from "react"
import { KeyRound, Pencil, Plus } from "lucide-react"
import { toast } from "sonner"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { PasswordInput } from "@/components/ui/password-input"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDate } from "@/lib/format"
import { useRoles } from "@/services/roles"
import { useCreateUser, useResetPassword, useUpdateUser, useUsers } from "@/services/users"
import type { Role } from "@/types/role"
import type { User } from "@/types/user"

const IMS_ROLES = ["INVENTORY_STAFF", "INVENTORY_MANAGER"] as const
const ROLE_HELP: Record<string, string> = {
  INVENTORY_STAFF: "View inventory data and record stock inward, transfers, returns and adjustments.",
  INVENTORY_MANAGER:
    "Everything staff can do, plus warehouses, packaging, conversions, external parties, opening balances and Shopify refresh.",
}

function RolePicker({
  roles,
  value,
  onChange,
}: {
  roles: Role[]
  value: string
  onChange: (id: string) => void
}) {
  return (
    <div className="space-y-2" role="radiogroup" aria-label="IMS role">
      {roles.map((r) => (
        <label
          key={r.id}
          className={`flex cursor-pointer items-start gap-2 rounded-md border p-3 text-sm ${value === r.id ? "border-primary bg-primary/5" : ""}`}
        >
          <input
            type="radio"
            name="ims-role"
            checked={value === r.id}
            onChange={() => onChange(r.id)}
            className="mt-1"
          />
          <span>
            <span className="font-medium">{r.name.replace("INVENTORY_", "Inventory ").toLowerCase().replace(/(^| )\w/g, (c) => c.toUpperCase())}</span>
            <span className="text-muted-foreground block text-xs">{ROLE_HELP[r.name]}</span>
          </span>
        </label>
      ))}
    </div>
  )
}

function CreateDialog({ roles, onClose }: { roles: Role[]; onClose: () => void }) {
  const create = useCreateUser()
  const [form, setForm] = React.useState({ name: "", email: "", phone: "", password: "", roleId: roles[0]?.id ?? "" })
  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }))
  const valid = form.name.trim() && /.+@.+\..+/.test(form.email) && form.password.length >= 8 && form.roleId

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New IMS user</DialogTitle>
          <DialogDescription>Creates a standard OMS account limited to the inventory workspace.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="iu-name">Full name</Label>
            <Input id="iu-name" value={form.name} onChange={(e) => set("name", e.target.value)} />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="iu-email">Email / username</Label>
              <Input id="iu-email" type="email" value={form.email} onChange={(e) => set("email", e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="iu-phone">Mobile number</Label>
              <Input id="iu-phone" value={form.phone} onChange={(e) => set("phone", e.target.value)} />
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="iu-pass">Initial password (min. 8 characters)</Label>
            <PasswordInput id="iu-pass" value={form.password} onChange={(e) => set("password", e.target.value)} autoComplete="new-password" />
          </div>
          <div className="space-y-1">
            <Label>Role</Label>
            <RolePicker roles={roles} value={form.roleId} onChange={(id) => set("roleId", id)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            disabled={!valid || create.isPending}
            onClick={() =>
              create.mutate(
                { name: form.name.trim(), email: form.email.trim(), phone: form.phone.trim() || null, password: form.password, role_ids: [form.roleId] },
                {
                  onSuccess: () => { toast.success("IMS user created."); onClose() },
                  onError: (e) => toast.error(getApiErrorMessage(e)),
                }
              )
            }
          >
            Create user
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function EditDialog({ user, roles, allRoles, onClose }: { user: User; roles: Role[]; allRoles: Role[]; onClose: () => void }) {
  const update = useUpdateUser(user.id)
  const [name, setName] = React.useState(user.name)
  const [phone, setPhone] = React.useState(user.phone ?? "")
  const [active, setActive] = React.useState(user.is_active)
  const [roleId, setRoleId] = React.useState(roles.find((r) => user.roles.includes(r.name))?.id ?? roles[0]?.id ?? "")
  // keep any non-IMS roles the account already holds
  const others = user.roles.filter((n) => !IMS_ROLES.includes(n as (typeof IMS_ROLES)[number]))
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit IMS user</DialogTitle>
          <DialogDescription>{user.email}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="eu-name">Full name</Label>
            <Input id="eu-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="eu-phone">Mobile number</Label>
            <Input id="eu-phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>Role</Label>
            <RolePicker roles={roles} value={roleId} onChange={setRoleId} />
            {others.length > 0 && <p className="text-muted-foreground text-xs">Other roles on this account ({others.join(", ")}) are kept.</p>}
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
            Active — inactive accounts cannot sign in
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            disabled={!name.trim() || update.isPending}
            onClick={() => {
              const otherIds = allRoles.filter((r) => others.includes(r.name)).map((r) => r.id)
              update.mutate(
                { name: name.trim(), phone: phone.trim() || null, is_active: active, role_ids: [roleId, ...otherIds] },
                { onSuccess: () => { toast.success("User updated."); onClose() }, onError: (e) => toast.error(getApiErrorMessage(e)) }
              )
            }}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ResetDialog({ user, onClose }: { user: User; onClose: () => void }) {
  const reset = useResetPassword(user.id)
  const [pw, setPw] = React.useState("")
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reset password</DialogTitle>
          <DialogDescription>
            Sets a new password for {user.email} and signs them out of every session. Share the password through a secure channel.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1">
          <Label htmlFor="rp-pass">New password (min. 8 characters)</Label>
          <PasswordInput id="rp-pass" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            disabled={pw.length < 8 || reset.isPending}
            onClick={() =>
              reset.mutate(pw, {
                onSuccess: () => { toast.success("Password reset."); onClose() },
                onError: (e) => toast.error(getApiErrorMessage(e)),
              })
            }
          >
            Reset password
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function StatusToggle({ user }: { user: User }) {
  const update = useUpdateUser(user.id)
  return (
    <Button
      size="sm"
      variant="ghost"
      disabled={update.isPending}
      onClick={() =>
        update.mutate(
          { is_active: !user.is_active },
          {
            onSuccess: () => toast.success(user.is_active ? "Account deactivated." : "Account activated."),
            onError: (e) => toast.error(getApiErrorMessage(e)),
          }
        )
      }
    >
      {user.is_active ? "Deactivate" : "Activate"}
    </Button>
  )
}

export default function ImsUsersPage() {
  const { hasPermission } = useAuth()
  const canManage = hasPermission("users.manage")
  const users = useUsers({ page: 1, pageSize: 200 })
  const rolesQuery = useRoles()
  const [creating, setCreating] = React.useState(false)
  const [editing, setEditing] = React.useState<User | null>(null)
  const [resetting, setResetting] = React.useState<User | null>(null)
  const [search, setSearch] = React.useState("")

  const imsRoles = (rolesQuery.data ?? []).filter((r) => IMS_ROLES.includes(r.name as (typeof IMS_ROLES)[number]))

  if (!canManage) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Access restricted</AlertTitle>
        <AlertDescription>You don&apos;t have permission to manage users.</AlertDescription>
      </Alert>
    )
  }

  const rows = (users.data?.data ?? []).filter(
    (u) =>
      u.roles.some((r) => IMS_ROLES.includes(r as (typeof IMS_ROLES)[number])) &&
      (!search || `${u.name} ${u.email}`.toLowerCase().includes(search.toLowerCase()))
  )

  const columns: DataTableColumn<User>[] = [
    { id: "name", header: "Name", cell: (u) => <span className="font-medium">{u.name}</span> },
    { id: "email", header: "Email", cell: (u) => u.email },
    { id: "phone", header: "Mobile", cell: (u) => u.phone ?? "—" },
    {
      id: "role",
      header: "IMS role",
      cell: (u) => (
        <Badge variant={u.roles.includes("INVENTORY_MANAGER") ? "default" : "secondary"}>
          {u.roles.includes("INVENTORY_MANAGER") ? "Manager" : "Staff"}
        </Badge>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (u) => <Badge variant={u.is_active ? "outline" : "destructive"}>{u.is_active ? "Active" : "Inactive"}</Badge>,
    },
    { id: "created", header: "Created", cell: (u) => formatDate(u.created_at) },
    {
      id: "actions",
      header: "",
      cell: (u) => (
        <div className="flex justify-end gap-1">
          <Button size="sm" variant="outline" onClick={() => setEditing(u)}>
            <Pencil className="size-3.5" /> Edit
          </Button>
          <Button size="sm" variant="outline" onClick={() => setResetting(u)}>
            <KeyRound className="size-3.5" /> Reset password
          </Button>
          <StatusToggle user={u} />
        </div>
      ),
    },
  ]

  return (
    <div className="space-y-4">
      <PageHeader
        title="IMS Users"
        description="Inventory workspace accounts. They use the same OMS sign-in and land on /ims."
        backHref="/users"
        backLabel="All users"
        actions={
          <Button onClick={() => setCreating(true)} disabled={imsRoles.length === 0}>
            <Plus className="size-4" /> New IMS user
          </Button>
        }
      />
      {rolesQuery.isSuccess && imsRoles.length === 0 && (
        <Alert>
          <AlertTitle>IMS roles not found</AlertTitle>
          <AlertDescription>Run the seed script so INVENTORY_STAFF and INVENTORY_MANAGER exist.</AlertDescription>
        </Alert>
      )}
      <Input className="w-72" placeholder="Search name or email…" value={search} onChange={(e) => setSearch(e.target.value)} />
      <QueryStates
        isLoading={users.isLoading}
        isError={users.isError}
        error={users.error}
        data={users.data}
        onRetry={() => users.refetch()}
        isEmpty={() => rows.length === 0}
        emptyTitle="No IMS users yet"
        emptyDescription="Create an Inventory Staff or Inventory Manager account to get started."
      >
        {() => <DataTable columns={columns} data={rows} rowKey={(u) => u.id} />}
      </QueryStates>
      {creating && <CreateDialog roles={imsRoles} onClose={() => setCreating(false)} />}
      {editing && <EditDialog user={editing} roles={imsRoles} allRoles={rolesQuery.data ?? []} onClose={() => setEditing(null)} />}
      {resetting && <ResetDialog user={resetting} onClose={() => setResetting(null)} />}
    </div>
  )
}
