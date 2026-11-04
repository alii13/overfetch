export const api = {
  getUser: () => fetch("/v1/me").then((r) => r.json()),
  listOrgs: () => fetch("/v1/orgs").then((r) => r.json()),
  getAgent: (id: string) => fetch(`/api/v1/agents/${id}`).then((r) => r.json()),
  listAgents: (params: { orgId: string; cursor?: string; limit: number }) =>
    fetch(`/api/v1/agents?org=${params.orgId}&limit=${params.limit}${params.cursor ? `&cursor=${params.cursor}` : ""}`).then((r) => r.json()),
  listAgentRuns: (id: string) => fetch(`/api/v1/agents/${id}/runs`).then((r) => r.json()),
  listAgentNames: (ids: string[]) =>
    fetch(`/api/v1/agents?ids=${ids.join(",")}`).then((r) => r.json()),
}
