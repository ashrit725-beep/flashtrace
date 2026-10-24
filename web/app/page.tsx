import type { Metadata } from "next";
import NewAuditScreen from "@/components/NewAuditScreen";

export const metadata: Metadata = { title: "New audit" };

export default function Page() {
  return <NewAuditScreen />;
}
