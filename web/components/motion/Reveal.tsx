"use client";

import { motion } from "motion/react";
import type { ReactNode } from "react";
import { DUR, PAGE_CLOCK, SHIFT, useInViewArrival, useMotionTokens } from "@/lib/motion";

// Ported from Magic UI blur-fade (MCP registry: registry/magicui/blur-fade.tsx).
// Kept: the opacity + small y variants and a delay.
// Stripped or changed (web/MOTION.md section 5):
// - the blur filter, entirely: it is neither transform nor opacity, and on
//   evidence it would alter captured pixels;
// - the 6 px default offset becomes 4 px (SHIFT.xs), 0 for evidence;
// - AnimatePresence exit replays and the `inView=false` default;
// - Tailwind and cn().
// Added: `initial` is false under reduced motion (the first frame is final),
// and in-view groups use useInViewArrival (amount "some", revealed at once on
// focus and before printing). Used only for content that arrives with data,
// never for server-rendered chrome.

type Tag = "div" | "section" | "li" | "p";
const TAGS = { div: motion.div, section: motion.section, li: motion.li, p: motion.p } as const;

interface Props {
  children: ReactNode;
  as?: Tag;
  className?: string;
  /** "mount": plays when the content arrives. "inview": waits until it is on screen. */
  when?: "mount" | "inview";
  delay?: number;
  duration?: number;
  /** Rise in px; 0 for a pure fade. */
  y?: number;
  id?: string;
  "aria-labelledby"?: string;
  role?: string;
}

export default function Reveal({
  children,
  as = "div",
  className,
  when = "mount",
  delay = 0,
  duration = DUR.base,
  y = SHIFT.xs,
  ...rest
}: Props) {
  const m = useMotionTokens();
  const inview = when === "inview";
  const [ref, arrival] = useInViewArrival<HTMLElement>(inview && !m.reduce);
  const Component = TAGS[as];
  const hidden = { opacity: 0, y };
  const shown = { opacity: 1, y: 0 };
  // In-view content starts in its final state and is hidden only once the
  // observer has reported it off screen (MOTION.md principle 7).
  const waiting = inview && arrival === "waiting";
  const transition = !inview || arrival === "play" ? m.tween(duration, { delay }) : { duration: 0 };
  return (
    <Component
      {...PAGE_CLOCK}
      // Each TAGS entry is a motion element; the ref type differs per tag.
      ref={ref as never}
      className={className}
      data-arrive=""
      initial={inview ? false : m.from(hidden)}
      animate={waiting ? hidden : shown}
      transition={transition}
      {...rest}
    >
      {children}
    </Component>
  );
}
