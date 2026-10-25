"use client";

import { AnimatePresence, motion, useIsPresent } from "motion/react";
import type { ReactNode } from "react";
import { DUR, PAGE_CLOCK, useMotionTokens } from "@/lib/motion";

// Ported from Magic UI animated-list (MCP registry: registry/magicui/animated-list.tsx).
// Kept: AnimatePresence, so a removed item fades out where it was.
// Stripped or changed (web/MOTION.md 3.4, sections 5 and 8):
// - the setTimeout loop that reveals one item per second (periodic);
// - scale: 0 entrances (never scale from 0) and the bouncy spring;
// - the staggered entrance: the rows were already there on every visit to
//   this utility list, so a rise told the user nothing (feel review);
// - SAFETY: the `layout` glide that closed the gap. At 390 px one Delete slid
//   the full-width cards below up by about 195 px over 550 ms, and every glyph
//   and edge crossed its cells on the same frames: 7 flash events in a quarter
//   of a second from one ordinary press. The gap now closes at once, when the
//   exit fade has finished (MOTION.md 3.4's fallback): each cell below changes
//   once.
// A leaving item is inert and aria-hidden, and its descendants can ask
// useIsPresent() (the Delete button drops data-delete) so focus code never
// picks it. Both lists use the default presence mode: the leaving item keeps
// its place until its fade ends, so nothing slides under it.

interface ListProps {
  children: ReactNode;
  /** Called when a leaving item has gone (instantly under reduced motion): the rows below close the gap now. */
  onExitComplete?: () => void;
}

export function AnimatedRows({ children, onExitComplete }: ListProps) {
  return (
    <AnimatePresence initial={false} onExitComplete={onExitComplete}>
      {children}
    </AnimatePresence>
  );
}

interface ItemProps {
  as: "tr" | "li";
  className?: string;
  children: ReactNode;
}

export function AnimatedItem({ as, className, children }: ItemProps) {
  const m = useMotionTokens();
  const present = useIsPresent();
  const Component = as === "tr" ? motion.tr : motion.li;
  return (
    <Component
      {...PAGE_CLOCK}
      className={className}
      initial={false}
      animate={{ opacity: 1 }}
      // display: none in the exit's own end state: the gap closes on the animation frame the fade
      // ends, not when React later removes the item in a task of its own (which could land a frame
      // apart in two captures of the same delete: self-audit F4).
      exit={{ opacity: 0, transitionEnd: { display: "none" }, transition: m.tween(DUR.quick) }}
      inert={!present}
      aria-hidden={present ? undefined : true}
    >
      {children}
    </Component>
  );
}
