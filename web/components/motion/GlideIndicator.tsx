"use client";

import { motion } from "motion/react";
import { useMotionTokens } from "@/lib/motion";

// Shared-element glide for the nav underline (web/MOTION.md 3.0): the 2 px
// rule moves on one axis between two items of one row, at a constant height
// and with no tint, so it reads correctly mid-glide.
// Source: Motion `layoutId` (motion skill, "Layout animations": one element
// moving between two places). Changed: spring bounce 0 (no overshoot), every
// layoutId must carry a "route:component" namespace, and the move is instant
// under reduced motion or when a keyboard started it.
// Not used for the radio cards any more (MOTION.md 8): there the indicator
// crossed the cards in between (showing the wrong option as selected) and
// its ring, a box-shadow from a CSS class, is not scale-corrected by Motion.

interface Props {
  /** Namespaced, e.g. "nav:underline" or "new:viewport-indicator". */
  layoutId: `${string}:${string}`;
  className?: string;
  /** True when the change came from the keyboard: no movement. */
  instant?: boolean;
}

/** Render it inside the current item only; Motion glides it from the previous item. */
export default function GlideIndicator({ layoutId, className, instant = false }: Props) {
  const m = useMotionTokens();
  return (
    <motion.span
      layoutId={layoutId}
      className={className}
      aria-hidden="true"
      transition={{ layout: m.glide(instant) }}
      initial={false}
    />
  );
}
