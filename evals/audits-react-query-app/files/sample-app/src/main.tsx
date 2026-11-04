import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { createRouter, RouterProvider } from "@tanstack/react-router"
import { createRoot } from "react-dom/client"

import { routeTree } from "./routeTree.gen"

// Planted: no default staleTime, no retry policy.
const queryClient = new QueryClient()
const router = createRouter({ routeTree })

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={queryClient}>
    <RouterProvider router={router} />
  </QueryClientProvider>,
)
