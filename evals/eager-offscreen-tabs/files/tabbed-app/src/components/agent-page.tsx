import { useQuery } from "@tanstack/react-query"
import { useState } from "react"

import { api } from "@/lib/api"
import { agentKeys } from "@/lib/query-keys"

import { LogsDialog } from "./logs-dialog"

type Tab = "overview" | "runs" | "settings"

export function AgentPage({ id }: { id: string }) {
  const [tab, setTab] = useState<Tab>("overview")
  const [logsFor, setLogsFor] = useState<string | null>(null)

  const agent = useQuery({ queryKey: agentKeys.detail(id), queryFn: () => api.getAgent(id) })
  const runs = useQuery({ queryKey: agentKeys.runs(id), queryFn: () => api.listRuns(id) })
  const settings = useQuery({ queryKey: agentKeys.settings(id), queryFn: () => api.getSettings(id) })

  return (
    <section>
      <h1>{agent.data?.name ?? "…"}</h1>
      <nav>
        <button onClick={() => setTab("overview")}>Overview</button>
        <button onClick={() => setTab("runs")}>Runs</button>
        <button onClick={() => setTab("settings")}>Settings</button>
      </nav>

      {tab === "overview" ? <p>{agent.data?.description}</p> : null}

      {tab === "runs" ? (
        <ul>
          {(runs.data ?? []).map((run) => (
            <li key={run.id}>
              {run.startedAt} {run.status}
              <button onClick={() => setLogsFor(run.id)}>Logs</button>
            </li>
          ))}
        </ul>
      ) : null}

      {tab === "settings" ? (
        <dl>
          <dt>Model</dt>
          <dd>{settings.data?.model}</dd>
          <dt>Max turns</dt>
          <dd>{settings.data?.maxTurns}</dd>
        </dl>
      ) : null}

      {logsFor ? <LogsDialog runId={logsFor} onClose={() => setLogsFor(null)} /> : null}
    </section>
  )
}
