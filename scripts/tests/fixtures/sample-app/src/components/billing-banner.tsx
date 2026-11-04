import { useBillingSummary } from "@/lib/use-billing-summary"

export function BillingBanner({ orgId }: { orgId: string }) {
  const { data } = useBillingSummary(orgId)
  if (data?.status !== "low") return null
  return <div role="status">Credits are running low</div>
}
