import { BorrowerPortal } from "@/components/portal/BorrowerPortal";
import { RoleGate } from "@/components/portal/PortalShell";

export const metadata = {
  title: "Borrower portal · AgriTrust AI",
};

export default function BorrowerPage() {
  return (
    <RoleGate role="borrower">
      <BorrowerPortal />
    </RoleGate>
  );
}
