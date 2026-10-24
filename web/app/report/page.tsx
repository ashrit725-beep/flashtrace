import type { Metadata } from "next";
import { Suspense } from "react";
import ReportScreen from "@/components/report/ReportScreen";
import { LoadingState } from "@/components/StatusViews";

export const metadata: Metadata = { title: "Report" };

// ?id= is only known in the browser in a static export, hence <Suspense>.
export default function Page() {
  return (
    <Suspense fallback={<LoadingState label="Loading the report" />}>
      <ReportScreen />
    </Suspense>
  );
}
