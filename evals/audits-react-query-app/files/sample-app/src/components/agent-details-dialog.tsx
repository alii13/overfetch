import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { useFlag } from "@/lib/flags"

// Planted: lazily loaded and flag-gated, with an unconditional 10 s poll on the run history.
export default function AgentDetailsDialog({ agentId, onClose }: { agentId: string; onClose: () => void }) {
  const showRuns = useFlag("agent-run-history")
  const { data: runs } = useQuery({
    queryKey: ["agent-runs", agentId],
    queryFn: () => api.listAgentRuns(agentId),
    refetchInterval: 10_000,
    enabled: showRuns,
  })
  return (
    <dialog open onClose={onClose}>
      <p>{agentId}</p>
      {showRuns ? <ol>{(runs?.items ?? []).map((run) => <li key={run.id}>{run.status}</li>)}</ol> : null}
    </dialog>
  )
}
