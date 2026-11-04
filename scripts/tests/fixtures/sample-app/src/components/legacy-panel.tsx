import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import { agentKeys } from "@/lib/query-keys"
import { api } from "@/lib/api"

export function LegacyPanel({ agentIds }: { agentIds: string[] }) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<string>("")

  // Planted: a hand-written fetch in an effect.
  useEffect(() => {
    fetch("/v1/status")
      .then((r) => r.json())
      .then((json) => setStatus(json.status))
  }, [])

  // Planted: a poll by setInterval that is never cleared.
  useEffect(() => {
    setInterval(() => {
      fetch("/v1/status").then((r) => r.json()).then((json) => setStatus(json.status))
    }, 5000)
  }, [])

  const warm = (id: string) =>
    queryClient.prefetchQuery({ queryKey: agentKeys.detail(id), queryFn: () => api.getAgent(id) })

  return (
    <ul>
      {agentIds.map((id) => (
        <li key={id} onMouseEnter={() => warm(id)}>
          {id} {status}
        </li>
      ))}
    </ul>
  )
}
