"use client"

import * as React from "react"

import { FulfillmentPaymentOrdersTable } from "@/components/analytics/fulfillment-payment-orders-table"
import { QueryStates } from "@/components/shared/query-states"
import { SelectableStatCard } from "@/components/shared/selectable-stat-card"
import { formatMoney } from "@/lib/format"
import { formatStatusLabel } from "@/lib/status-styles"
import {
  useFulfillmentPaymentClassification,
  useNdrReasonBreakdown,
} from "@/services/analytics"
import type { FulfillmentPaymentBucket } from "@/types/analytics"

interface FulfillmentPaymentClassificationPanelProps {
  title: string
  fulfillmentStatus: "fulfilled" | "unfulfilled"
  bucket: FulfillmentPaymentBucket
  dateFrom: string
  dateTo: string
}

/** One Fulfilled/Unfulfilled card family: the Total/COD/Prepaid amount
 * KPIs (requirement 1's "clickable Total/COD/Prepaid KPIs ... amount and
 * percentage"), expanding on a COD/Prepaid click into the shipment-status
 * classification (requirement 1's "detailed classification ... order
 * count, amount and percentage ... clickable"), which itself expands on a
 * classification click into the matching order list. The `ndr` row
 * additionally shows real NDR reasons (or "Unknown/Unclassified") instead
 * of the raw classification list, since that is the one place genuine
 * "why unfulfilled" text exists in this schema.
 */
export function FulfillmentPaymentClassificationPanel({
  title,
  fulfillmentStatus,
  bucket,
  dateFrom,
  dateTo,
}: FulfillmentPaymentClassificationPanelProps) {
  const [selectedPaymentType, setSelectedPaymentType] = React.useState<
    "cod" | "prepaid" | null
  >(null)
  const [selectedStatus, setSelectedStatus] = React.useState<string | null>(null)

  const classificationQuery = useFulfillmentPaymentClassification({
    date_from: dateFrom,
    date_to: dateTo,
    fulfillmentStatus,
    paymentType: selectedPaymentType ?? "cod",
    enabled: selectedPaymentType !== null,
  })
  const ndrQuery = useNdrReasonBreakdown({
    date_from: dateFrom,
    date_to: dateTo,
    fulfillmentStatus,
    paymentType: selectedPaymentType ?? undefined,
    enabled: selectedStatus === "ndr" && selectedPaymentType !== null,
  })

  function togglePaymentType(value: "cod" | "prepaid") {
    setSelectedStatus(null)
    setSelectedPaymentType((current) => (current === value ? null : value))
  }

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold">{title}</h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <SelectableStatCard
          label="Total"
          value={formatMoney(bucket.amount)}
          detail={`${bucket.order_count.toLocaleString("en-IN")} orders · ${bucket.percent_of_total_amount.toFixed(1)}% of all orders`}
          onClick={() => undefined}
          accent="blue"
          className="cursor-default"
        />
        <SelectableStatCard
          label="COD"
          value={formatMoney(bucket.cod.amount)}
          detail={`${bucket.cod.order_count.toLocaleString("en-IN")} orders · ${bucket.cod.percent_of_bucket_amount.toFixed(1)}% of ${title}`}
          selected={selectedPaymentType === "cod"}
          onClick={() => togglePaymentType("cod")}
          accent="amber"
        />
        <SelectableStatCard
          label="Prepaid"
          value={formatMoney(bucket.prepaid.amount)}
          detail={`${bucket.prepaid.order_count.toLocaleString("en-IN")} orders · ${bucket.prepaid.percent_of_bucket_amount.toFixed(1)}% of ${title}`}
          selected={selectedPaymentType === "prepaid"}
          onClick={() => togglePaymentType("prepaid")}
          accent="green"
        />
      </div>

      {selectedPaymentType && (
        <QueryStates
          isLoading={classificationQuery.isLoading}
          isError={classificationQuery.isError}
          error={classificationQuery.error}
          data={classificationQuery.data}
          onRetry={() => classificationQuery.refetch()}
        >
          {(data) => (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {data.classifications.map((row) => (
                <SelectableStatCard
                  key={row.unified_status}
                  label={formatStatusLabel(row.unified_status)}
                  value={row.order_count.toLocaleString("en-IN")}
                  detail={`${formatMoney(row.amount)} · ${row.percent_of_slice_amount.toFixed(1)}%`}
                  selected={selectedStatus === row.unified_status}
                  onClick={() =>
                    setSelectedStatus((current) =>
                      current === row.unified_status ? null : row.unified_status
                    )
                  }
                  accent="violet"
                  className="text-sm"
                />
              ))}
            </div>
          )}
        </QueryStates>
      )}

      {selectedStatus === "ndr" && selectedPaymentType && (
        <QueryStates
          isLoading={ndrQuery.isLoading}
          isError={ndrQuery.isError}
          error={ndrQuery.error}
          data={ndrQuery.data}
          onRetry={() => ndrQuery.refetch()}
          isEmpty={(d) => d.reasons.length === 0}
          emptyTitle="No NDR reasons recorded"
        >
          {(data) => (
            <div className="rounded-lg border p-3">
              <p className="mb-2 text-xs font-medium text-muted-foreground">
                NDR reasons ({data.ndr_order_count} orders, {formatMoney(data.ndr_amount)})
              </p>
              <ul className="space-y-1 text-sm">
                {data.reasons.map((r) => (
                  <li key={r.reason} className="flex items-center justify-between gap-2">
                    <span>{r.reason}</span>
                    <span className="text-muted-foreground tabular-nums">
                      {r.order_count} · {formatMoney(r.amount)} · {r.percent_of_ndr_amount.toFixed(1)}%
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </QueryStates>
      )}

      {selectedStatus && selectedPaymentType && (
        <FulfillmentPaymentOrdersTable
          key={`${fulfillmentStatus}-${selectedPaymentType}-${selectedStatus}`}
          title={`${title} · ${selectedPaymentType.toUpperCase()} · ${formatStatusLabel(selectedStatus)}`}
          dateFrom={dateFrom}
          dateTo={dateTo}
          fulfillmentStatus={fulfillmentStatus}
          paymentType={selectedPaymentType}
          unifiedStatus={selectedStatus}
        />
      )}
    </div>
  )
}
