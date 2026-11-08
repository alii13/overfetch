import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { runKeys } from "@/lib/query-keys"

export function LogsDialog({ runId, onClose }: { runId: string; onClose: () => void }) {
  const logs = useQuery({ queryKey: runKeys.logs(runId), queryFn: () => api.getRunLogs(runId) })
  return (
    <dialog open>
      <button onClick={onClose}>Close</button>
      {logs.isPending ? <p>Loading…</p> : null}
      <pre>{(logs.data ?? []).map((line) => `${line.at} ${line.text}`).join("\n")}</pre>
    </dialog>
  )
}
