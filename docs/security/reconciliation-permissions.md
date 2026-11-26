# Remittance reconciliation permissions (Modules 6 & 7)

Status: **permissions defined; only `ADMIN` holds the two new write
permissions.** Nothing below is granted to any other role without an explicit
decision by the business owner.

## The three permissions

| Permission | What it allows | New? |
|---|---|---|
| `reconciliation.read` | View the COD / Prepaid dashboards, KPI and percentage drilldowns, all reports, the unmatched list, bank credits, the list of uploaded files, download an original uploaded file, and export Excel. | Existing |
| `reconciliation.upload` | Upload a COD remittance report, a gateway settlement report or a bank statement; preview a file's columns; download the upload templates. Uploads are stored unchanged and de-duplicated. | **New** |
| `reconciliation.adjust` | Change evidence: manually match / unmatch a remittance or settlement line to an order, **manually verify bank money** (link a bank credit to a remittance or settlement reference, or verify a Shiprocket remittance's bank credit), record or reverse a reasoned adjustment, re-run matching. | **New** |

`reconciliation.manage` (existing) is **not** used by Modules 6 & 7. It keeps
its original meaning: trigger the Shopify/Shiprocket data-sync reconciliation
run and resolve its mismatches. Holding it gives no access to remittance
evidence, and vice versa.

**Behaviour change at release:**
`POST /revenue-reconciliation/remittances/{awb}/verify-bank-credit` previously
required `reconciliation.manage`, which `OPERATIONS` holds. It now requires
`reconciliation.adjust` (**ADMIN only** by default), because it makes money
count as bank confirmed. It also requires a `reason`, in addition to `amount`,
`reference` and `credited_on`; any script calling it must send one.

## Manual bank verification

Both routes require a reason, the bank reference and an amount, and are audit
logged. Their money is shown as **Manually verified**, separately from
**Statement-matched** credits.

| Route | Limits |
|---|---|
| Link a statement credit to a report reference (`/{cod\|prepaid}/bank-links`) | Not above what is unallocated on the credit, nor what the reference still needs |
| Verify a Shiprocket remittance (endpoint above) | Not above the reported remittance, nor what is unallocated on a statement credit with the same reference |

Without a statement credit, a Shiprocket verification rests on the ADMIN's
statement alone. If a statement with that reference is imported later, the
counted amount is capped at the statement amount.

`is_superuser` users bypass permission checks entirely (see `docs/security/rbac.md`).

## Who holds what today

| Role | read | upload | adjust | Why |
|---|:-:|:-:|:-:|---|
| `ADMIN` | yes | yes | yes | Holds every permission (`"*"`). The migration also grants the two new ones to `ADMIN` explicitly. |
| `OPERATIONS` | yes (already had it) | **no** | **no** | Needs to see remittance status. Uploading files and changing evidence is a finance control, not an operations one. |
| `MANAGEMENT` | yes (already had it) | no | no | Read-only reporting. |
| `CUSTOMER_SUPPORT`, `MARKETING`, `TELECALLER`, `TEAM_LEADER`, `SHIPMENT_STAFF`, `IMS_*`, `INVENTORY_*` | no | no | no | No reason to see settlement or bank data. |
| `FULFILLMENT` | no | no | no | `docs/security/rbac.md` already states: no reconciliation. |

The migration (`c9e1a5d3f7b2`) and `scripts/seed.py` encode exactly this table.
`apps/api/tests/test_remittance_manual_match_and_uploads.py` fails if any other role ever gains
`upload` or `adjust` by accident.

## Recommendation (needs your decision -- not applied)

- Keep `reconciliation.upload` and `reconciliation.adjust` with `ADMIN` until
  you decide who does finance reconciliation.
- If a finance / accounts person (not a full admin) should do this work, create
  a dedicated role (for example `FINANCE`) from **Administration -> Roles** and
  give it `reconciliation.read` + `reconciliation.upload`. Give
  `reconciliation.adjust` only to the person accountable for write-offs and
  manual matches: an adjustment can close a shortfall and a manual match can
  attach a remittance to an order, so it is the highest-risk permission here.
- Separation of duties: ideally the person who uploads reports is not the
  only person who can adjust. Both are audit-logged (below), so this is a
  policy choice rather than a technical limit.
- Do **not** add these permissions to `OPERATIONS` or `MANAGEMENT` by default.

## Audit trail

Every write is recorded in the existing audit log (`/audit-logs`), with the
acting user, before/after values and, for money, the amounts:

`remittance.cod.upload`, `remittance.prepaid.upload`, `remittance.bank.upload`,
`remittance.cod.manual_match`, `remittance.cod.unmatch`,
`remittance.prepaid.manual_match`, `remittance.prepaid.unmatch`,
`remittance.cod.rematch`, `remittance.prepaid.rematch`,
`remittance.cod.bank_link` / `bank_unlink`, `remittance.prepaid.bank_link` /
`bank_unlink`, `remittance.cod.bank_verification` (Shiprocket),
`remittance.cod.adjustment` / `adjustment_reversed`,
`remittance.prepaid.adjustment` / `adjustment_reversed`.

A manual COD match additionally records the line amount, the order's expected
amount, the resulting difference and status, and whether the user accepted a
difference. Links and adjustments are reversed, never deleted.

## Applying permissions in production

1. Run the migrations in order: `c9e1a5d3f7b2` (creates the tables, inserts
   the two permissions, grants them to `ADMIN`), `d3b5f7a9c1e2` (gateway sync)
   and `e7a1c3b5d9f4` (two nullable manual-verification columns). It must be run through the normal
   migration process -- rendering it as offline SQL does not inline the
   permission values.
2. Decide who else needs access using the recommendation above, then assign
   via **Administration -> Roles**. No code change is needed.
3. Users must log in again (or refresh) to pick up new permissions.
