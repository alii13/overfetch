import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { agentKeys } from "@/lib/query-keys"
import { AgentRow } from "./agent-row"

export function AgentList({ orgId }: { orgId: string }) {
  // Planted: staleTime 0 on the list.
  const { data } = useQuery({
    queryKey: agentKeys.list(orgId),
    queryFn: () => api.listAgents({ orgId, limit: 50 }),
    staleTime: 0,
  })
  return (
    <ul>
      {(data?.items ?? []).map((agent) => (
        <AgentRow key={agent.id} agentId={agent.id} />
      ))}
    </ul>
  )
}
