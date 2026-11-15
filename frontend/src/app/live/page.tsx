import type { Metadata } from "next";
import { Suspense } from "react";
import LiveFeed from "@/components/LiveFeed";

export const metadata: Metadata = { title: "Buoy live" };

// The feed reads the clock to say how old each sample is, so it renders per visit, not at build
export default function LivePage() {
  return (
    <Suspense fallback={null}>
      <LiveFeed />
    </Suspense>
  );
}
