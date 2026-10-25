"use client";

import { motion } from "motion/react";
import { DUR, EASE, useMotionTokens } from "@/lib/motion";

// One-shot path draws: the stepper's check, the verdict seal, the Verified
// badge's check. Source: Motion `pathLength` (motion skill). No looping dash
// offsets; under reduced motion the path is complete on the first frame.

const PATHS = {
  check: "M6.5 12.5l3.5 3.5 7.5-8",
  cross: "M8 8l8 8M16 8l-8 8",
  question: "M9.2 9.2a2.9 2.9 0 1 1 4.1 2.6c-.9.4-1.3 1-1.3 1.9v.6M12 17.2v.1",
} as const;

export type Glyph = keyof typeof PATHS;

interface Props {
  glyph: Glyph;
  /** Pixel size of the square SVG (viewBox 24). */
  size?: number;
  /** false: draw nothing, show the final glyph (it was already there before this screen saw it change). */
  draw?: boolean;
  delay?: number;
  duration?: number;
  /** Also draw a circle around the glyph (the verdict seal). */
  ring?: boolean;
  strokeWidth?: number;
  className?: string;
}

export default function DrawPath({ glyph, size = 24, draw = true, delay = 0, duration = DUR.base, ring = false, strokeWidth = 2, className }: Props) {
  const m = useMotionTokens();
  const animated = draw && !m.reduce;
  const line = (extraDelay: number) => ({
    initial: animated ? { pathLength: 0 } : false,
    animate: { pathLength: 1 },
    transition: animated ? { duration, ease: EASE.draw, delay: delay + extraDelay } : { duration: 0 },
  });
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false" data-arrive="">
      {ring && (
        <motion.circle cx="12" cy="12" r="10.5" fill="none" stroke="currentColor" strokeWidth={strokeWidth * 0.75} {...line(0)} />
      )}
      <motion.path
        d={PATHS[glyph]}
        fill="none"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        {...line(ring ? 0.06 : 0)}
      />
    </svg>
  );
}
