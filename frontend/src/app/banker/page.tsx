import { BankerUnderwritingDashboard } from "@/components/portal/BankerUnderwritingDashboard";
import { RoleGate } from "@/components/portal/PortalShell";

export const metadata = {
  title: "Underwriting desk · AgriTrust AI",
};

export default function BankerPage() {
  return (
    <RoleGate role="banker">
      <BankerUnderwritingDashboard />
    </RoleGate>
  );
}
