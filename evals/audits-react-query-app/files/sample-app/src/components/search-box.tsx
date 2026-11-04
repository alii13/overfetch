import { useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

export function SearchBox() {
  const queryClient = useQueryClient()
  const [value, setValue] = useState("")
  return (
    <input
      value={value}
      onChange={(event) => {
        setValue(event.target.value)
        // Planted: blanket invalidation on every keystroke.
        void queryClient.invalidateQueries()
      }}
    />
  )
}
