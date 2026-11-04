import { useQuery } from "@tanstack/react-query"

import { billingKeys } from "./query-keys"

export const BILLING_POLL_MS = 60_000

export interface BillingSummary {
  credits: number
  status: "ok" | "low" | "out"
}

// Planted: a poll option inside a hook that several components call.
export function useBillingSummary(orgId: string) {
  return useQuery({
    queryKey: billingKeys.summary(orgId),
    queryFn: () => fetchBillingSummary(orgId),
    staleTime: 30_000,
    refetchInterval: BILLING_POLL_MS,
  })
}

async function fetchBillingSummary(orgId: string): Promise<BillingSummary> {
  const res = await fetch(`/v1/billing/summary?org=${orgId}`)
  return res.json()
}
