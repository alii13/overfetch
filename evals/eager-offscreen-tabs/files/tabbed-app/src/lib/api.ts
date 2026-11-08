export interface Agent {
  id: string
  name: string
  description: string
}

export interface Run {
  id: string
  startedAt: string
  status: "running" | "succeeded" | "failed"
}

export interface AgentSettings {
  model: string
  maxTurns: number
}

export interface LogLine {
  at: string
  text: string
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw Object.assign(new Error(res.statusText), { status: res.status })
  return res.json() as Promise<T>
}

export const api = {
  getAgent: (id: string) => fetch(`/api/v1/agents/${id}`).then(json<Agent>),
  listRuns: (id: string) => fetch(`/api/v1/agents/${id}/runs?limit=50`).then(json<Run[]>),
  getSettings: (id: string) => fetch(`/api/v1/agents/${id}/settings`).then(json<AgentSettings>),
  getRunLogs: (runId: string) => fetch(`/api/v1/runs/${runId}/logs`).then(json<LogLine[]>),
}
