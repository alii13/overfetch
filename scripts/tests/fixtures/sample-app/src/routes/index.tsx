import { createFileRoute } from "@tanstack/react-router"

import { AgentList } from "@/components/agent-list"
import { SearchBox } from "@/components/search-box"

export const Route = createFileRoute("/")({
  component: () => (
    <main>
      <SearchBox />
      <AgentList orgId="org_example" />
    </main>
  ),
})
