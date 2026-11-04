import { useBillingSummary } from "@/lib/use-billing-summary"

export function BillingSettings({ orgId }: { orgId: string }) {
  const { data } = useBillingSummary(orgId)
  return <p>{data?.credits ?? 0} credits</p>
}
