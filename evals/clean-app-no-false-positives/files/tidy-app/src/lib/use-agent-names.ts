import { useQueries } from "@tanstack/react-query"
import { useMemo } from "react"

import { api } from "./api"
import { agentKeys } from "./query-keys"

const CHUNK = 100
export const AGENT_NAME_STALE_TIME = 5 * 60_000

/**
 * Display names for a set of agents, keyed by id. One request per chunk of 100
 * ids, not one per row. An id that does not resolve is absent from the map so
 * the caller falls back to showing the id.
 */
export function useAgentNames(ids: readonly string[]): Map<string, string> {
  const unique = useMemo(() => [...new Set(ids)].sort(), [ids])
  const chunks = useMemo(() => {
    const out: string[][] = []
    for (let i = 0; i < unique.length; i += CHUNK) out.push(unique.slice(i, i + CHUNK))
    return out
  }, [unique])
  const results = useQueries({
    queries: chunks.map((chunk) => ({
      queryKey: agentKeys.names(chunk),
      queryFn: () => api.listAgentNames(chunk),
      staleTime: AGENT_NAME_STALE_TIME,
    })),
  })
  return useMemo(() => {
    const names = new Map<string, string>()
    for (const result of results) {
      for (const row of result.data?.items ?? []) names.set(row.id, row.name)
    }
    return names
  }, [results])
}
