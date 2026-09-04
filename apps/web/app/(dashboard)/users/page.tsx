"use client"

import * as React from "react"
import { toast } from "sonner"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
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
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDate } from "@/lib/format"
import { usePaginationState } from "@/lib/use-pagination"
import { useRoles } from "@/services/roles"
import {
  useCreateUser,
  useDeactivateUser,
  useUpdateUser,
  useUsers,
} from "@/services/users"
import type { Role } from "@/types/role"
import type { User } from "@/types/user"

// GET /users has no `q` search param, so this is the largest single page
// the backend allows (`page_size<=200`) -- see services/users.ts.
const USERS_FETCH_PAGE_SIZE = 200

function RoleCheckboxList({
  roles,
  selectedIds,
  onToggle,
}: {
  roles: Role[]
  selectedIds: Set<string>
  onToggle: (id: string) => void
}) {
  if (roles.length === 0) {
    return <p className="text-muted-foreground text-sm">No roles exist yet.</p>
  }

  return (
    <div className="flex max-h-56 flex-col gap-2 overflow-y-auto rounded-lg border p-3">
      {roles.map((role) => (
        <label key={role.id} className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={selectedIds.has(role.id)}
            onCheckedChange={() => onToggle(role.id)}
          />
          {role.name}
          {role.description && (
            <span className="text-muted-foreground text-xs">— {role.description}</span>
          )}
        </label>
      ))}
    </div>
  )
}

function useRoleSelection(initialIds: string[] = []) {
  const [selectedIds, setSelectedIds] = React.useState<Set<string>>(
    () => new Set(initialIds)
  )
  const toggle = React.useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])
  return { selectedIds, setSelectedIds, toggle }
}

function CreateUserDialog({ roles }: { roles: Role[] }) {
  const { hasPermission } = useAuth()
  const [open, setOpen] = React.useState(false)
  const [name, setName] = React.useState("")
  const [email, setEmail] = React.useState("")
  const [phone, setPhone] = React.useState("")
  const [password, setPassword] = React.useState("")
  const { selectedIds, setSelectedIds, toggle } = useRoleSelection()
  const createUser = useCreateUser()

  if (!hasPermission("users.manage")) return null

  function reset() {
    setName("")
    setEmail("")
    setPhone("")
    setPassword("")
    setSelectedIds(new Set())
  }

  function submit() {
    createUser.mutate(
      {
        name,
        email,
        phone: phone || null,
        password,
        role_ids: Array.from(selectedIds),
      },
      {
        onSuccess: () => {
          toast.success("User created.")
          setOpen(false)
          reset()
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  const canSubmit = Boolean(name && email && password.length >= 8)

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) reset()
      }}
    >
      <Button size="sm" onClick={() => setOpen(true)}>
        New User
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New User</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="user-name">Name</Label>
            <Input
              id="user-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="user-email">Email</Label>
            <Input
              id="user-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="user-phone">Phone (optional)</Label>
            <Input
              id="user-phone"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="user-password">Password</Label>
            <Input
              id="user-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 8 characters"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Roles</Label>
            <RoleCheckboxList roles={roles} selectedIds={selectedIds} onToggle={toggle} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button disabled={!canSubmit || createUser.isPending} onClick={submit}>
            {createUser.isPending ? "Creating..." : "Create User"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function EditUserDialog({ user, roles }: { user: User; roles: Role[] }) {
  const { hasPermission } = useAuth()
  const [open, setOpen] = React.useState(false)
  const [name, setName] = React.useState(user.name)
  const [phone, setPhone] = React.useState(user.phone ?? "")
  const initialIds = React.useMemo(
    () => roles.filter((r) => user.roles.includes(r.name)).map((r) => r.id),
    [roles, user.roles]
  )
  const { selectedIds, setSelectedIds, toggle } = useRoleSelection(initialIds)
  const updateUser = useUpdateUser(user.id)

  if (!hasPermission("users.manage")) return null

  function openDialog() {
    setName(user.name)
    setPhone(user.phone ?? "")
    setSelectedIds(new Set(initialIds))
    setOpen(true)
  }

  function submit() {
    updateUser.mutate(
      {
        name,
        phone: phone || null,
        role_ids: Array.from(selectedIds),
      },
      {
        onSuccess: () => {
          toast.success("User updated.")
          setOpen(false)
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button variant="outline" size="sm" onClick={openDialog}>
        Edit
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit User</DialogTitle>
          <DialogDescription>{user.email}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`user-name-${user.id}`}>Name</Label>
            <Input
              id={`user-name-${user.id}`}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`user-phone-${user.id}`}>Phone (optional)</Label>
            <Input
              id={`user-phone-${user.id}`}
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Roles</Label>
            <RoleCheckboxList roles={roles} selectedIds={selectedIds} onToggle={toggle} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button disabled={!name || updateUser.isPending} onClick={submit}>
            {updateUser.isPending ? "Saving..." : "Save Changes"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function UserStatusAction({ user }: { user: User }) {
  const { hasPermission } = useAuth()
  const updateUser = useUpdateUser(user.id)
  const deactivateUser = useDeactivateUser()

  if (!hasPermission("users.manage")) return null

  if (user.is_active) {
    return (
      <Button
        variant="outline"
        size="sm"
        disabled={deactivateUser.isPending}
        onClick={() =>
          deactivateUser.mutate(user.id, {
            onSuccess: () => toast.success("User deactivated."),
            onError: (error) => toast.error(getApiErrorMessage(error)),
          })
        }
      >
        {deactivateUser.isPending ? "Deactivating..." : "Deactivate"}
      </Button>
    )
  }

  return (
    <Button
      variant="outline"
      size="sm"
      disabled={updateUser.isPending}
      onClick={() =>
        updateUser.mutate(
          { is_active: true },
          {
            onSuccess: () => toast.success("User activated."),
            onError: (error) => toast.error(getApiErrorMessage(error)),
          }
        )
      }
    >
      {updateUser.isPending ? "Activating..." : "Activate"}
    </Button>
  )
}

function AccessRestricted() {
  return (
    <Alert variant="destructive">
      <AlertTitle>Access restricted</AlertTitle>
      <AlertDescription>
        You don&apos;t have permission to view Users. Contact an administrator if you
        believe this is a mistake.
      </AlertDescription>
    </Alert>
  )
}

function UsersPageContent() {
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const [search, setSearch] = React.useState("")

  const usersQuery = useUsers({ page: 1, pageSize: USERS_FETCH_PAGE_SIZE })
  const rolesQuery = useRoles()
  const roles = rolesQuery.data ?? []

  const filtered = React.useMemo(() => {
    const all = usersQuery.data?.data ?? []
    const q = search.trim().toLowerCase()
    if (!q) return all
    return all.filter(
      (user) =>
        user.name.toLowerCase().includes(q) || user.email.toLowerCase().includes(q)
    )
  }, [usersQuery.data, search])

  const pageData = React.useMemo(() => {
    const start = (page - 1) * pageSize
    return filtered.slice(start, start + pageSize)
  }, [filtered, page, pageSize])

  const meta = {
    page,
    page_size: pageSize,
    total_items: filtered.length,
    total_pages: Math.max(1, Math.ceil(filtered.length / pageSize)),
  }

  const columns: DataTableColumn<User>[] = [
    {
      id: "name",
      header: "Name",
      cell: (user) => <span className="font-medium">{user.name}</span>,
    },
    { id: "email", header: "Email", cell: (user) => user.email },
    {
      id: "status",
      header: "Status",
      cell: (user) => (
        <Badge variant={user.is_active ? "default" : "secondary"}>
          {user.is_active ? "Active" : "Inactive"}
        </Badge>
      ),
    },
    {
      id: "roles",
      header: "Roles",
      cell: (user) => (
        <div className="flex flex-wrap gap-1">
          {user.roles.length === 0 ? (
            <span className="text-muted-foreground">None</span>
          ) : (
            user.roles.map((role) => (
              <Badge key={role} variant="outline">
                {role}
              </Badge>
            ))
          )}
        </div>
      ),
    },
    {
      id: "created",
      header: "Created",
      cell: (user) => formatDate(user.created_at),
    },
    {
      id: "actions",
      header: "",
      cell: (user) => (
        <div className="flex items-center gap-2">
          <EditUserDialog user={user} roles={roles} />
          <UserStatusAction user={user} />
        </div>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="Users"
        description={
          usersQuery.data
            ? `${usersQuery.data.meta.total_items} user(s).`
            : "Manage staff accounts and their assigned roles."
        }
        actions={<CreateUserDialog roles={roles} />}
      />
      <div className="flex flex-col gap-4">
        <Input
          value={search}
          onChange={(e) => {
            setSearch(e.target.value)
            resetPage()
          }}
          placeholder="Search by name or email..."
          className="max-w-xs"
        />
        <QueryStates
          isLoading={usersQuery.isLoading}
          isError={usersQuery.isError}
          error={usersQuery.error}
          data={usersQuery.data}
          onRetry={() => void usersQuery.refetch()}
          isEmpty={() => filtered.length === 0}
          emptyTitle={search ? "No users match your search" : "No users yet"}
          emptyDescription={
            search ? "Try a different name or email." : "Create a user to get started."
          }
        >
          {() => (
            <>
              <DataTable columns={columns} data={pageData} rowKey={(user) => user.id} />
              <PaginationBar meta={meta} onPageChange={setPage} />
            </>
          )}
        </QueryStates>
      </div>
    </>
  )
}

export default function UsersPage() {
  const { hasPermission, isLoading } = useAuth()

  if (isLoading) return null

  if (!hasPermission("users.manage")) {
    return (
      <>
        <PageHeader title="Users" />
        <AccessRestricted />
      </>
    )
  }

  return <UsersPageContent />
}
