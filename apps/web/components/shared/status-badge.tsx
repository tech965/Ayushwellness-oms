import { Badge } from "@/components/ui/badge"
import {
  formatIntegrationStatusLabel,
  formatStatusLabel,
  getStatusTone,
  STATUS_TONE_CLASSES,
  type StatusDomain,
} from "@/lib/status-styles"
import { cn } from "@/lib/utils"

interface StatusBadgeProps {
  domain: StatusDomain
  status: string
  className?: string
}

export function StatusBadge({ domain, status, className }: StatusBadgeProps) {
  const tone = getStatusTone(domain, status)
  const label =
    domain === "integration"
      ? formatIntegrationStatusLabel(status)
      : formatStatusLabel(status)
  return (
    <Badge
      variant="outline"
      className={cn("border-transparent", STATUS_TONE_CLASSES[tone], className)}
    >
      {label}
    </Badge>
  )
}
