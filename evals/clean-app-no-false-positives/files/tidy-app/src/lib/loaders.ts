import type { QueryClient } from "@tanstack/react-query"

import { api } from "./api"
import { orgKeys } from "./query-keys"

export async function loadShell(queryClient: QueryClient) {
  const [user, orgs] = await Promise.all([
    queryClient.ensureQueryData({ queryKey: orgKeys.me(), queryFn: api.getUser }),
    queryClient.ensureQueryData({ queryKey: orgKeys.list(), queryFn: api.listOrgs }),
  ])
  return { user, orgs }
}
