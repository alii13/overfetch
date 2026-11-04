import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"

export function NamesA({ ids }: { ids: string[] }) {
  // Planted: inline key spelled one way here...
  const { data } = useQuery({ queryKey: ["agentNames", ids], queryFn: () => api.listAgentNames(ids) })
  return <span>{data?.items.length ?? 0}</span>
}
