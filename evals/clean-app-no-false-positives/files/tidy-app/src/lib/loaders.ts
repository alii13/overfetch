import type { QueryClient } from "@tanstack/react-query"

import { api } from "./api"
import { orgKeys } from "./query-keys"

// Route loader: the two reads are independent, so they start together and the
// component's useQuery calls on the same keys find warm entries.
export async function loadShell(queryClient: QueryClient) {
  const [user, orgs] = await Promise.all([
    queryClient.ensureQueryData({ queryKey: orgKeys.me(), queryFn: api.getUser }),
    queryClient.ensureQueryData({ queryKey: orgKeys.list(), queryFn: api.listOrgs }),
  ])
  return { user, orgs }
}
