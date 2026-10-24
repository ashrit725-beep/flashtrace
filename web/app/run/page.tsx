import type { Metadata } from "next";
import { Suspense } from "react";
import RunScreen from "@/components/RunScreen";
import { LoadingState } from "@/components/StatusViews";

export const metadata: Metadata = { title: "Running audit" };

// The job ID is in the query string (?id=), which a static export only
// knows in the browser, so the screen must sit inside <Suspense>.
export default function Page() {
  return (
    <Suspense fallback={<LoadingState label="Loading the audit" />}>
      <RunScreen />
    </Suspense>
  );
}
