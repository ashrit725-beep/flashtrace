"use client";

import { AnimatePresence, motion, useIsPresent } from "motion/react";
import type { ReactNode } from "react";
import { DUR, PAGE_CLOCK, useMotionTokens } from "@/lib/motion";

// Label and icon swaps in place ("Run audit" -> "Starting…", copy icon ->
// check, "Not verified" -> "Verified", "…" -> "Done. Opening the report…").
// Source: make-interfaces-feel-better "Icon swaps: cross-fade with opacity,
// scale" and Motion AnimatePresence mode="popLayout". Stripped: the blur
// (neither transform nor opacity). The leaving label is aria-hidden and taken
// out of the layout at once, so the accessible name and textContent only ever
// hold the current label, and the box keeps a fixed width set by its owner.

interface Props {
  /** Identity of the content: a new value crossfades. */
  value: string;
  children: ReactNode;
  className?: string;
  /** Start the entering content slightly smaller (icons): scale 0.8 -> 1. */
  scaleFrom?: number;
  /** Play nothing for the first render (true) or fade the first content in too (false). */
  skipFirst?: boolean;
  /** inline-flex (default) lines an icon up with its text; inline-block keeps the spaces of running text. */
  display?: "inline-flex" | "inline-block";
}

interface FaceProps {
  children: ReactNode;
  className?: string;
  scaleFrom: number;
  display: "inline-flex" | "inline-block";
}

function Face({ children, className, scaleFrom, display }: FaceProps) {
  const m = useMotionTokens();
  const present = useIsPresent();
  return (
    <motion.span
      {...PAGE_CLOCK}
      className={className}
      aria-hidden={present ? undefined : true}
      initial={m.from({ opacity: 0, scale: scaleFrom })}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: scaleFrom, transition: m.tween(DUR.press) }}
      transition={m.tween(DUR.quick)}
      style={display === "inline-flex" ? { display, alignItems: "center", gap: "inherit" } : { display }}
    >
      {children}
    </motion.span>
  );
}

export default function CrossfadeLabel({ value, children, className, scaleFrom = 1, skipFirst = true, display = "inline-flex" }: Props) {
  return (
    <AnimatePresence initial={!skipFirst} mode="popLayout">
      <Face key={value} className={className} scaleFrom={scaleFrom} display={display}>
        {children}
      </Face>
    </AnimatePresence>
  );
}
