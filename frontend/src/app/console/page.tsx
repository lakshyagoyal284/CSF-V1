import { UnderwritingDashboard } from "@/components/UnderwritingDashboard";
import { RoleGate } from "@/components/portal/PortalShell";

/**
 * Legacy single-portal scoring console.
 *
 * Kept as a model playground for the hackathon demo. The backend now applies the
 * banker role check to /api/score and /api/simulate as well, so this page is
 * gated and reuses the portal session token. The verified, audited workflow is
 * at /banker.
 */
export default function LegacyConsolePage() {
  return (
    <RoleGate role="banker">
      <UnderwritingDashboard />
    </RoleGate>
  );
}
