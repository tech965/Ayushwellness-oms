/** Where a freshly signed-in user lands. Only IMS users are sent somewhere
 * other than `/dashboard`; every other role keeps the existing flow (the
 * `RoleRedirect` in the dashboard layout still routes Telecaller/Team
 * Leader/etc.). A user who also holds one of those OMS section roles is left
 * on that existing flow so their priority is unchanged.
 *
 * UX only -- every IMS API is permission-checked server-side.
 */
const OMS_SECTION_ROLES = ["TEAM_LEADER", "TELECALLER", "FULFILLMENT", "SHIPMENT_STAFF"]
const IMS_ROLES = ["IMS_USER", "INVENTORY_STAFF", "INVENTORY_MANAGER"]

export function roleHome(roles: string[] | undefined, isSuperuser = false): string {
  const held = roles ?? []
  if (isSuperuser || OMS_SECTION_ROLES.some((r) => held.includes(r))) return "/dashboard"
  return IMS_ROLES.some((r) => held.includes(r)) ? "/ims" : "/dashboard"
}
