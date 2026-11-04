export const agentKeys = {
  root: () => ["agents"] as const,
  list: (orgId: string) => ["agents", "list", orgId] as const,
  detail: (id: string) => ["agents", "detail", id] as const,
  names: (ids: readonly string[]) => ["agents", "names", ...ids] as const,
}

export const orgKeys = {
  root: () => ["org"] as const,
  me: () => ["org", "me"] as const,
  list: () => ["org", "list"] as const,
}
