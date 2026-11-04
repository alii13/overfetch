import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"

export function NamesB({ ids }: { ids: string[] }) {
  // ...and another way here, so the same read lives in two cache entries.
  const { data } = useQuery({ queryKey: ["agent-names", ids], queryFn: () => api.listAgentNames(ids) })
  return <span>{data?.items.length ?? 0}</span>
}
