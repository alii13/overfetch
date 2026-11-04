import { api } from "./api"

export interface Agent {
  id: string
  name: string
}

// Planted: two independent awaits in sequence.
export async function loadHome(orgId: string) {
  const user = await api.getUser()
  const orgs = await api.listOrgs()
  return { user, orgs, orgId }
}

// Planted: one request per id with no ceiling.
export async function loadAgentDetails(ids: string[]) {
  const agents = await Promise.all(ids.map((id) => api.getAgent(id)))
  return agents
}

// Planted: fetch-everything page size and a loop over every page.
export async function loadAllAgents(orgId: string): Promise<Agent[]> {
  const out: Agent[] = []
  let cursor: string | undefined
  while (true) {
    const page = await api.listAgents({ orgId, cursor, limit: 1000 })
    out.push(...page.items)
    cursor = page.nextCursor
    if (!cursor) break
  }
  return out
}
