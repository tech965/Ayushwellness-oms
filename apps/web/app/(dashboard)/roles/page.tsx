"use client"

import * as React from "react"
import { toast } from "sonner"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { PageHeader } from "@/components/shared/page-header"
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
import { Textarea } from "@/components/ui/textarea"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { usePermissions } from "@/services/permissions"
import { useCreateRole, useRoles, useUpdateRole } from "@/services/roles"
import type { Permission } from "@/types/permission"
import type { Role } from "@/types/role"

function groupPermissionsByModule(permissions: Permission[]): [string, Permission[]][] {
  const groups = new Map<string, Permission[]>()
  for (const permission of permissions) {
    const list = groups.get(permission.module) ?? []
    list.push(permission)
    groups.set(permission.module, list)
  }
  return Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b))
}

function PermissionCheckboxList({
  selectedIds,
  onToggle,
}: {
  selectedIds: Set<string>
  onToggle: (id: string) => void
}) {
  const query = usePermissions()

  if (query.isLoading) {
    return <p className="text-muted-foreground text-sm">Loading permissions…</p>
  }
  if (query.isError) {
    return <p className="text-destructive text-sm">{getApiErrorMessage(query.error)}</p>
  }

  const groups = groupPermissionsByModule(query.data ?? [])

  return (
    <div className="flex max-h-72 flex-col gap-4 overflow-y-auto rounded-lg border p-3">
      {groups.map(([module, modulePermissions]) => (
        <div key={module} className="flex flex-col gap-1.5">
          <p className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
            {module}
          </p>
          {modulePermissions.map((permission) => (
            <label key={permission.id} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={selectedIds.has(permission.id)}
                onCheckedChange={() => onToggle(permission.id)}
              />
              <span className="font-mono text-xs">{permission.code}</span>
              {permission.description && (
                <span className="text-muted-foreground text-xs">
                  — {permission.description}
                </span>
              )}
            </label>
          ))}
        </div>
      ))}
    </div>
  )
}

function usePermissionSelection(initialIds: string[] = []) {
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

function CreateRoleDialog() {
  const { hasPermission } = useAuth()
  const [open, setOpen] = React.useState(false)
  const [name, setName] = React.useState("")
  const [description, setDescription] = React.useState("")
  const { selectedIds, setSelectedIds, toggle } = usePermissionSelection()
  const createRole = useCreateRole()

  if (!hasPermission("roles.manage")) return null

  function reset() {
    setName("")
    setDescription("")
    setSelectedIds(new Set())
  }

  function submit() {
    createRole.mutate(
      {
        name,
        description: description || null,
        permission_ids: Array.from(selectedIds),
      },
      {
        onSuccess: () => {
          toast.success("Role created.")
          setOpen(false)
          reset()
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) reset()
      }}
    >
      <Button size="sm" onClick={() => setOpen(true)}>
        New Role
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New Role</DialogTitle>
          <DialogDescription>
            Role name can&apos;t be changed once created — choose it carefully.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="role-name">Name</Label>
            <Input
              id="role-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="role-description">Description (optional)</Label>
            <Textarea
              id="role-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Permissions</Label>
            <PermissionCheckboxList selectedIds={selectedIds} onToggle={toggle} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button disabled={!name || createRole.isPending} onClick={submit}>
            {createRole.isPending ? "Creating..." : "Create Role"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function EditRoleAction({
  role,
  allPermissions,
}: {
  role: Role
  allPermissions: Permission[]
}) {
  const { hasPermission } = useAuth()
  const [open, setOpen] = React.useState(false)
  const [description, setDescription] = React.useState(role.description ?? "")
  const initialIds = React.useMemo(
    () =>
      allPermissions.filter((p) => role.permissions.includes(p.code)).map((p) => p.id),
    [allPermissions, role.permissions]
  )
  const { selectedIds, setSelectedIds, toggle } = usePermissionSelection(initialIds)
  const updateRole = useUpdateRole(role.id)

  if (!hasPermission("roles.manage")) return null

  function openDialog() {
    setDescription(role.description ?? "")
    setSelectedIds(new Set(initialIds))
    setOpen(true)
  }

  function submit() {
    updateRole.mutate(
      {
        description: description || null,
        permission_ids: Array.from(selectedIds),
      },
      {
        onSuccess: () => {
          toast.success("Role updated.")
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
          <DialogTitle>Edit Role</DialogTitle>
          <DialogDescription>
            Only description and permissions can be changed — the role name is fixed.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`role-name-${role.id}`}>Name</Label>
            <Input id={`role-name-${role.id}`} value={role.name} disabled />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`role-description-${role.id}`}>Description (optional)</Label>
            <Textarea
              id={`role-description-${role.id}`}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Permissions</Label>
            <PermissionCheckboxList selectedIds={selectedIds} onToggle={toggle} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button disabled={updateRole.isPending} onClick={submit}>
            {updateRole.isPending ? "Saving..." : "Save Changes"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function RolesTable() {
  const query = useRoles()
  const permissionsQuery = usePermissions()
  const allPermissions = permissionsQuery.data ?? []

  const columns: DataTableColumn<Role>[] = [
    {
      id: "name",
      header: "Name",
      cell: (role) => <span className="font-medium">{role.name}</span>,
    },
    {
      id: "description",
      header: "Description",
      cell: (role) =>
        role.description || <span className="text-muted-foreground">—</span>,
    },
    {
      id: "permissions",
      header: "Permissions",
      cell: (role) => (
        <div className="flex max-w-md flex-wrap gap-1">
          {role.permissions.length === 0 ? (
            <span className="text-muted-foreground">None</span>
          ) : (
            role.permissions.map((code) => (
              <Badge key={code} variant="outline" className="font-mono text-[0.65rem]">
                {code}
              </Badge>
            ))
          )}
        </div>
      ),
    },
    {
      id: "action",
      header: "",
      cell: (role) => <EditRoleAction role={role} allPermissions={allPermissions} />,
    },
  ]

  return (
    <QueryStates
      isLoading={query.isLoading}
      isError={query.isError}
      error={query.error}
      data={query.data}
      onRetry={() => void query.refetch()}
      isEmpty={(data) => data.length === 0}
      emptyTitle="No roles yet"
      emptyDescription="Create a role to start assigning permissions to users."
    >
      {(data) => <DataTable columns={columns} data={data} rowKey={(role) => role.id} />}
    </QueryStates>
  )
}

function AccessRestricted() {
  return (
    <Alert variant="destructive">
      <AlertTitle>Access restricted</AlertTitle>
      <AlertDescription>
        You don&apos;t have permission to view Roles. Contact an administrator if you
        believe this is a mistake.
      </AlertDescription>
    </Alert>
  )
}

export default function RolesPage() {
  const { hasPermission, isLoading } = useAuth()

  if (isLoading) return null

  if (!hasPermission("roles.manage")) {
    return (
      <>
        <PageHeader title="Roles" />
        <AccessRestricted />
      </>
    )
  }

  return (
    <>
      <PageHeader
        title="Roles"
        description="Roles bundle permissions together — assign them to users on the Users page."
        actions={<CreateRoleDialog />}
      />
      <RolesTable />
    </>
  )
}
