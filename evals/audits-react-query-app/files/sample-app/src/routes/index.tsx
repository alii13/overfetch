import { createFileRoute } from "@tanstack/react-router"

import { AgentList } from "@/components/agent-list"
import { CleanList } from "@/components/clean-list"
import { SearchBox } from "@/components/search-box"

export const Route = createFileRoute("/")({
  component: () => (
    <main>
      <SearchBox />
      <AgentList orgId="org_example" />
      <CleanList orgId="org_example" />
    </main>
  ),
})
