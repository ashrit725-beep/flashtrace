"use client";

import { animate, useMotionValue } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { DUR, EASE, useMotionTokens } from "@/lib/motion";

// Ported from Magic UI number-ticker (MCP registry: registry/magicui/number-ticker.tsx),
// with React Bits CountUp as the reference for counting from one value to another.
// Kept: a motion value written straight to textContent, tabular numerals.
// Stripped or changed (web/MOTION.md section 5):
// - useSpring (damping 60, stiffness 100 settles far past 600 ms) becomes a
//   bounded tween of DUR.emphasis, the token the progress fill beside it uses,
//   so the count and the bar it describes land on the same frame;
// - no count-up from 0 and no useInView start: this is "follow" mode only. It
//   ticks from the value it showed to the new one, because the values in
//   between really happened (frames that were captured). It is used for the
//   Running frame counter and nowhere else (MOTION.md principle 5);
// - CountUp's digit-roll mode, replay-on-hover (repeats), overshoot easing,
//   Tailwind and cn().
// Added: the digits are aria-hidden (the caller puts the exact value in
// visually hidden text), a fixed width in ch so nothing beside it moves, no
// tween when the value is unchanged or the tab is hidden, and an instant
// jump under reduced motion.

interface Props {
  value: number;
  /** Width in digits (ch); defaults to the digits of `value`. */
  digits?: number;
  className?: string;
}

export default function NumberTicker({ value, digits, className }: Props) {
  const ref = useRef<HTMLSpanElement>(null);
  const shown = useMotionValue(value);
  const m = useMotionTokens();
  // React renders the first value once; after that only the motion value writes the text.
  const [first] = useState(value);

  useEffect(
    () =>
      shown.on("change", (latest) => {
        if (ref.current) ref.current.textContent = String(Math.round(latest));
      }),
    [shown],
  );

  useEffect(() => {
    if (Math.round(shown.get()) === value) return;
    if (m.reduce || document.hidden) {
      shown.jump(value);
      return;
    }
    const controls = animate(shown, value, { duration: DUR.emphasis, ease: EASE.out });
    return () => controls.stop();
  }, [value, m.reduce, shown]);

  return (
    <span
      ref={ref}
      className={className}
      aria-hidden="true"
      style={{ display: "inline-block", minWidth: `${digits ?? String(value).length}ch`, textAlign: "right", fontVariantNumeric: "tabular-nums" }}
    >
      {first}
    </span>
  );
}
