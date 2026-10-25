"use client";

import { MotionConfig, MotionGlobalConfig } from "motion/react";
import { useEffect, type ReactNode } from "react";
import { readReducedMotion, useInAppReducedMotion, useReducedMotionPreference } from "@/lib/motion";

// SAFETY (web/MOTION.md section 2): reducedMotion="user" alone still plays
// opacity fades, so the global skip flag is set as well. The first assignment
// runs here, at module evaluation in the browser, not in an effect: React runs
// child effects before a parent's, so an effect would let a child's first
// animation start before the flag is set.
if (typeof window !== "undefined") MotionGlobalConfig.skipAnimations = readReducedMotion();

/**
 * Wraps the whole app (app/layout.tsx). Under reduced motion (OS setting or
 * the footer switch) every Motion animation is skipped: the final state shows
 * on the first frame and document.getAnimations() stays empty.
 */
export default function MotionRoot({ children }: { children: ReactNode }) {
  const reduce = useReducedMotionPreference();
  const forced = useInAppReducedMotion();

  useEffect(() => {
    MotionGlobalConfig.skipAnimations = readReducedMotion();
  }, [reduce]);

  return (
    <MotionConfig
      reducedMotion={forced ? "always" : "user"}
      skipAnimations={reduce}
      transition={reduce ? { duration: 0 } : undefined}
    >
      {children}
    </MotionConfig>
  );
}
