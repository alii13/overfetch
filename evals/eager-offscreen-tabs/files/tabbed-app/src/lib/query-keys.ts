export const agentKeys = {
  root: () => ["agents"] as const,
  detail: (id: string) => ["agents", "detail", id] as const,
  runs: (id: string) => ["agents", "runs", id] as const,
  settings: (id: string) => ["agents", "settings", id] as const,
}

export const runKeys = {
  logs: (runId: string) => ["runs", "logs", runId] as const,
}
