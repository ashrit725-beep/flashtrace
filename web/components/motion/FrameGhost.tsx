"use client";

import { motion } from "motion/react";
import { DUR, EASE } from "@/lib/motion";

// The frame viewer's "grow from the thumbnail" (web/MOTION.md 3.3, section 5).
// Idea from Aceternity expandable-cards (MCP: grow a card into a dialog);
// motion from Motion's layout projection.
// Changed, for SAFETY: the evidence image itself never moves, scales or blurs.
// What travels is a ghost, a 1 px ink rectangle with no fill, from the
// thumbnail's picture to the viewer's picture (the frame as drawn inside the
// letterboxed <img>, so a 4:3 frame zooms into a 4:3 frame); the image fades
// in at its final place as the ghost lands. Both take DUR.quick, so the
// outline arrives on the frame the panel, veil and picture reach full opacity. A scaling high-contrast frame would sweep its
// edges across thousands of cells on the same frames and could flash by
// itself; an unfilled 1 px outline cannot.
// Stripped: Aceternity's near-black overlay, outside-click hook, layoutId
// (a native <dialog> enters the top layer in showModal(), after Motion would
// measure it) and Tailwind. The parent skips the ghost under reduced motion
// and for keyboard-opened or keyboard-closed viewers.
// Changed from a transform FLIP (Motion layout projection) to an SVG rect
// whose x, y, width and height are animated: nothing is scaled, so the 1 px
// stroke stays 1 px without correction, and the result does not depend on
// when Chromium re-rasterises a scaled layer (two self-audit captures of F1
// differed at scattered frames with the scaled version).

export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

export const boxOf = (el: Element): Box => {
  const r = el.getBoundingClientRect();
  return { left: r.left, top: r.top, width: r.width, height: r.height };
};

interface Props {
  from: Box;
  to: Box;
  className?: string;
  onDone: () => void;
}

const geometry = (b: Box) => ({ attrX: b.left, attrY: b.top, width: b.width, height: b.height });

/** Mount it inside the <dialog> (the top layer); it flies once from `from` to `to`, then calls onDone. */
export default function FrameGhost({ from, to, className, onDone }: Props) {
  return (
    <svg
      className={className}
      aria-hidden="true"
      focusable="false"
      style={{ position: "fixed", inset: 0, width: "100%", height: "100%", overflow: "visible" }}
    >
      <motion.rect
        initial={geometry(from)}
        animate={geometry(to)}
        transition={{ duration: DUR.quick, ease: EASE.out }}
        onAnimationComplete={onDone}
        fill="none"
        stroke="currentColor"
        strokeWidth={1}
      />
    </svg>
  );
}
