const enabled = new Set<string>(["agent-run-history"])

export function useFlag(name: string): boolean {
  return enabled.has(name)
}
