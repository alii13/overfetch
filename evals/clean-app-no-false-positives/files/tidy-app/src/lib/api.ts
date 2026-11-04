export interface Agent {
  id: string
  name: string
  status: "idle" | "creating" | "running"
}

export interface Page<T> {
  items: T[]
  nextCursor?: string
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw Object.assign(new Error(res.statusText), { status: res.status })
  return res.json() as Promise<T>
}

export const api = {
  getUser: () => fetch("/v1/me").then(json),
  listOrgs: () => fetch("/v1/orgs").then(json),
  getAgent: (id: string) => fetch(`/api/v1/agents/${id}`).then(json<Agent>),
  listAgents: (params: { orgId: string; cursor?: string; limit: number }) =>
    fetch(`/api/v1/agents?org=${params.orgId}&limit=${params.limit}${params.cursor ? `&cursor=${params.cursor}` : ""}`).then(json<Page<Agent>>),
  listAgentNames: (ids: readonly string[]) =>
    fetch(`/api/v1/agents?fields=id,name&ids=${ids.join(",")}`).then(json<Page<Pick<Agent, "id" | "name">>>),
  renameAgent: (id: string, name: string) =>
    fetch(`/api/v1/agents/${id}`, { method: "PATCH", body: JSON.stringify({ name }) }).then(json<Agent>),
}
