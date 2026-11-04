export const agentKeys = {
  root: () => ["agents"] as const,
  list: (orgId: string) => ["agents", "list", orgId] as const,
  detail: (id: string) => ["agents", "detail", id] as const,
  names: (ids: string[]) => ["agents", "names", ...ids] as const,
}

export const billingKeys = {
  root: () => ["billing"] as const,
  summary: (orgId: string) => ["billing", "summary", orgId] as const,
}
