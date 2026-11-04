import { useQuery } from "@tanstack/react-query"

import { AgentList } from "./components/agent-list"
import { api } from "./lib/api"
import { orgKeys } from "./lib/query-keys"

export function App() {
  const { data: user } = useQuery({ queryKey: orgKeys.me(), queryFn: api.getUser })
  const { data: orgs } = useQuery({ queryKey: orgKeys.list(), queryFn: api.listOrgs })
  const orgId = orgs?.[0]?.id
  return (
    <main>
      <h1>{user?.name ?? "…"}</h1>
      {orgId ? <AgentList orgId={orgId} /> : null}
    </main>
  )
}
