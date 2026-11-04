import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { agentKeys } from "@/lib/query-keys"
import { useAgentNames } from "@/lib/use-agent-names"

function AgentRow({ id, name, onRename }: { id: string; name: string; onRename: (id: string, name: string) => void }) {
  return (
    <li>
      <span>{name}</span>
      <button onClick={() => onRename(id, `${name} (copy)`)}>Rename</button>
    </li>
  )
}

export function AgentList({ orgId }: { orgId: string }) {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: agentKeys.list(orgId),
    queryFn: () => api.listAgents({ orgId, limit: 50 }),
    // Polls only while an agent is still being created; this list is the one owner of the poll.
    refetchInterval: (query) => (query.state.data?.items.some((a) => a.status === "creating") ? 5000 : false),
  })
  const ids = (data?.items ?? []).map((agent) => agent.id)
  const names = useAgentNames(ids)

  const rename = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => api.renameAgent(id, name),
    onSuccess: (updated) => {
      queryClient.setQueryData(agentKeys.detail(updated.id), updated)
      void queryClient.invalidateQueries({ queryKey: agentKeys.list(orgId) })
    },
  })

  return (
    <ul>
      {ids.map((id) => (
        <AgentRow key={id} id={id} name={names.get(id) ?? id} onRename={(id, name) => rename.mutate({ id, name })} />
      ))}
    </ul>
  )
}
