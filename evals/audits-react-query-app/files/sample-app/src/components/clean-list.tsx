import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { agentKeys } from "@/lib/query-keys"

// Clean: one batch read for all names, chunked to the backend's limit.
export function useAgentNames(ids: string[]): Map<string, string> {
  const chunks: string[][] = []
  for (let i = 0; i < ids.length; i += 100) chunks.push(ids.slice(i, i + 100))
  const results = useQueries({
    queries: chunks.map((chunk) => ({
      queryKey: agentKeys.names(chunk),
      queryFn: () => api.listAgentNames(chunk),
      staleTime: 5 * 60_000,
    })),
  })
  const names = new Map<string, string>()
  for (const result of results) {
    for (const row of result.data?.items ?? []) names.set(row.id, row.name)
  }
  return names
}

function CleanRow({ id, name }: { id: string; name: string }) {
  return <li>{name ?? id}</li>
}

export function CleanList({ orgId }: { orgId: string }) {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: agentKeys.list(orgId),
    queryFn: () => api.listAgents({ orgId, limit: 50 }),
    // Clean: polls only while a row is still being created, from one owner.
    refetchInterval: (query) => (query.state.data?.items.some((a) => a.status === "creating") ? 5000 : false),
  })
  const ids = (data?.items ?? []).map((agent) => agent.id)
  const names = useAgentNames(ids)
  return (
    <ul onClick={() => void queryClient.invalidateQueries({ queryKey: agentKeys.list(orgId) })}>
      {ids.map((id) => (
        <CleanRow key={id} id={id} name={names.get(id) ?? id} />
      ))}
    </ul>
  )
}
