import { createFileRoute } from "@tanstack/react-router"

import { LegacyPanel } from "@/components/legacy-panel"

export const Route = createFileRoute("/agents/$agentId")({
  component: function AgentPage() {
    const { agentId } = Route.useParams()
    return <LegacyPanel agentIds={[agentId]} />
  },
})
