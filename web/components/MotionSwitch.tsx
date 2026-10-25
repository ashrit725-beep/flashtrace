"use client";

import { setInAppReducedMotion, useInAppReducedMotion, useSystemReducedMotion } from "@/lib/motion";
import { useSafeToggle } from "@/lib/useSafeToggle";

/**
 * The in-app motion switch (web/MOTION.md 3.6; WCAG 2.3.3 without relying on
 * the operating system's setting). It changes how future changes are drawn;
 * on screen only its own ": on/off" suffix changes. Content that waited off
 * screen for an entrance and was scrolled into view while motion was reduced
 * stays shown when motion is turned back on (lib/motion useInViewArrival).
 * SAFETY: it still goes through a 350 ms gate and ignores a held Enter, like
 * every other toggle, so no future regression can make it a strobe source.
 */
export default function MotionSwitch() {
  const forced = useInAppReducedMotion();
  const system = useSystemReducedMotion();
  const safe = useSafeToggle();
  return (
    <p className="motion-switch">
      <button
        type="button"
        className="link-button"
        aria-pressed={forced}
        onKeyDown={safe.onKeyDown}
        onClick={() => safe.run(() => setInAppReducedMotion(!forced))}
      >
        Reduce motion<span aria-hidden="true">{forced ? ": on" : ": off"}</span>
      </button>
      {system && <span className="muted"> Your system setting already reduces motion here.</span>}
    </p>
  );
}
