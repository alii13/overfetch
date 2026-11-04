import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { agentKeys } from "@/lib/query-keys"

interface Props {
  agentId: string
}

// Planted: a per-row query that fetches the whole agent to print its name.
export function AgentRow({ agentId }: Props) {
  const { data: agent, isLoading } = useQuery({
    queryKey: agentKeys.detail(agentId),
    queryFn: () => api.getAgent(agentId),
  })
  if (isLoading) return <li>…</li>
  if (!agent) return null
  return <li>{agent.name}</li>
}
