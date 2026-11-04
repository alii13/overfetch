import { useQuery } from "@tanstack/react-query"
import { lazy, Suspense, useState } from "react"

import { api } from "@/lib/api"
import { agentKeys } from "@/lib/query-keys"
import { AgentRow } from "./agent-row"

// Loaded only when a row is opened, so a capture that never opens one never sees its requests.
const AgentDetailsDialog = lazy(() => import("./agent-details-dialog"))

export function AgentList({ orgId }: { orgId: string }) {
  const [openId, setOpenId] = useState<string | null>(null)
  // Planted: staleTime 0 on the list.
  const { data } = useQuery({
    queryKey: agentKeys.list(orgId),
    queryFn: () => api.listAgents({ orgId, limit: 50 }),
    staleTime: 0,
  })
  return (
    <>
      <ul onClick={(event) => setOpenId((event.target as HTMLElement).dataset.agentId ?? null)}>
        {(data?.items ?? []).map((agent) => (
          <AgentRow key={agent.id} agentId={agent.id} />
        ))}
      </ul>
      {openId ? (
        <Suspense fallback={null}>
          <AgentDetailsDialog agentId={openId} onClose={() => setOpenId(null)} />
        </Suspense>
      ) : null}
    </>
  )
}
