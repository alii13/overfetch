import { AgentList } from "./components/agent-list"
import { BillingBanner } from "./components/billing-banner"
import { BillingSettings } from "./components/billing-settings"
import { CleanList } from "./components/clean-list"
import { LegacyPanel } from "./components/legacy-panel"
import { SearchBox } from "./components/search-box"

export function App() {
  const orgId = "org_example"
  return (
    <main>
      <BillingBanner orgId={orgId} />
      <SearchBox />
      <AgentList orgId={orgId} />
      <CleanList orgId={orgId} />
      <LegacyPanel agentIds={["a", "b"]} />
      <BillingSettings orgId={orgId} />
    </main>
  )
}
