import { createRootRoute, Outlet } from "@tanstack/react-router"

import { BillingBanner } from "@/components/billing-banner"

export const Route = createRootRoute({
  component: () => (
    <>
      <BillingBanner orgId="org_example" />
      <Outlet />
    </>
  ),
})
