import { AgentPage } from "./components/agent-page"

export function App() {
  const agentId = new URLSearchParams(window.location.search).get("agent") ?? "agent_1"
  return (
    <main>
      <AgentPage id={agentId} />
    </main>
  )
}
