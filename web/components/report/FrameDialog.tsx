"use client";

import { motion } from "motion/react";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type SyntheticEvent } from "react";
import { flushSync } from "react-dom";
import { frameUrl, type Size } from "@/lib/api";
import { frameTime } from "@/lib/format";
import { DUR, GATE_MS, PAGE_CLOCK, useMotionTokens, useTrailingGate } from "@/lib/motion";
import { blockKeyRepeat, useSafeToggle } from "@/lib/useSafeToggle";
import FrameGhost, { boxOf, type Box } from "../motion/FrameGhost";
import styles from "./Report.module.css";

// SAFETY: the two states of a flashing element are often a white frame and a
// black frame. Holding an arrow key or Enter (auto-repeat, ~30 per second) or
// clicking fast would turn this viewer into a strobe. So steps are manual, key
// repeats are ignored, and useSafeToggle drops steps closer than
// GATE_MS.viewerStep (500 ms) apart: the viewer never changes frame more than
// twice a second. Opening counts as a change, so the first step comes 500 ms or
// more after the viewer is fully shown (the end of its opening fade): a fade
// can complete the opening's flashes up to 160 ms late, and counting from the
// click would then let open, two steps and a close fall inside one second. 500, not the 350 ms of other toggles, because closing can never
// be gated: with 350 ms, open, two steps and a close fitted into about 750 ms,
// and the self-audit measured exactly 3 flashes a second at 390 px (open, step
// at the earliest time, close as the second step shows, reopen when allowed).
//
// Motion (web/MOTION.md 3.3, section 8): for a pointer-opened viewer the panel,
// its picture and the paper veil fade in together while a 1 px ghost outline
// grows from the thumbnail to the picture, landing with them. A keyboard-opened
// viewer appears at once (no fade, no ghost), so focus never lands on a button
// that is still fading in. Closing is instant (see below). Frames never
// crossfade or move: a step swaps the picture instantly, and only once the
// next frame is decoded, so a slow load never shows an empty box between two
// frames.

interface Props {
  jobId: string;
  startIndex: number;
  frameCount: number;
  fps: number;
  viewport: Size;
  /** Labels of the evidence frames, shown when the viewer reaches one. */
  labels: Map<number, string>;
  /** The thumbnail's image box when a pointer opened the viewer; null for keyboard or a text button (no ghost). */
  origin: Box | null;
  /** True when the keyboard opened the viewer (a click with detail 0): it appears at once, with no fade. */
  keyboard: boolean;
  /** Where focus goes back to on close (the control that opened the viewer). */
  opener: HTMLElement | null;
  onClose: () => void;
}

/**
 * The picture shown for `src`: swapped only after the new frame is decoded,
 * and never more often than the step limit (decode times vary, so two quick
 * decodes could otherwise land closer than 350 ms apart). The last frame asked
 * for always wins.
 */
function useDecodedSrc(src: string): string {
  const [shown, setShown] = useState(src);
  const gate = useTrailingGate(GATE_MS.viewerStep);
  useEffect(() => {
    if (src === shown) return;
    let current = true;
    const next = new Image();
    next.src = src;
    next
      .decode()
      .catch(() => undefined) // a broken frame still swaps, so the counter and the picture agree
      .then(() => {
        // Swap on the page clock (a timer, not the decode's own moment) and commit at once,
        // so the frame in which the picture changes does not depend on decoding speed.
        if (current) setTimeout(() => current && gate.run(() => current && flushSync(() => setShown(src))), 0);
      });
    return () => {
      current = false;
    };
  }, [src, shown, gate]);
  return shown;
}

// Mounted only while open (the parent renders it conditionally), so every
// opening starts fresh at startIndex; unmounting restores focus.
//
// SAFETY: closing is instant (never gated, never animated). The self-audit
// (F1, the viewer opened and closed as fast as the gate allows) showed that a
// fading close completes the viewer's flashes (veil over a black thumbnail,
// panel text, the captured frame) on several separate frames, because each
// contrast crosses the threshold at a different point of the fade: three
// flash events in one close at 390 px. An instant close completes them all in
// one frame. The opening keeps its short fade and the ghost.
/**
 * Where the picture is drawn inside the viewer's <img>: the element is wider
 * than the frame on a desktop (width 100%, max-height 64vh, object-fit:
 * contain), so the frame sits centred in it, inside its 1 px border. The ghost
 * lands on this box, so the 4:3 thumbnail zooms into the 4:3 picture instead of
 * stretching onto the letterbox around it.
 */
function containedPicture(img: Element, frame: Size): Box {
  const b = boxOf(img);
  const scale = Math.min((b.width - 2) / frame.width, (b.height - 2) / frame.height);
  const width = frame.width * scale;
  const height = frame.height * scale;
  return { left: b.left + (b.width - width) / 2, top: b.top + (b.height - height) / 2, width, height };
}

export default function FrameDialog({ jobId, startIndex, frameCount, fps, viewport, labels, origin, keyboard, opener, onClose }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  const nextRef = useRef<HTMLButtonElement>(null);
  const safe = useSafeToggle(GATE_MS.viewerStep);
  const m = useMotionTokens();
  const [index, setIndex] = useState(startIndex);
  const [ghost, setGhost] = useState<{ from: Box; to: Box } | null>(null);
  const finished = useRef(false);
  const last = Math.max(0, frameCount - 1);
  const shownSrc = useDecodedSrc(frameUrl(jobId, index));
  const shownIndex = Number(shownSrc.match(/\/frames\/(\d+)\.png$/)?.[1] ?? index);

  // State changes that can come from outside an input event (the native close
  // event, frame and animation callbacks) are committed at once with
  // flushSync, inside the frame that caused them, so the viewer opens and
  // closes on the same frames every time.
  const finish = useCallback(() => {
    if (finished.current) return;
    finished.current = true;
    flushSync(onClose);
  }, [onClose]);

  useEffect(() => {
    const dialog = dialogRef.current;
    const restoreTo = opener ?? (document.activeElement as HTMLElement | null);
    if (dialog && !dialog.open) dialog.showModal(); // modal: the page behind becomes inert
    safe.run(() => true); // opening is a change: the first step waits out the limit
    nextRef.current?.focus();
    let frame = 0;
    if (origin && !keyboard && imageRef.current && !m.reduce) {
      const to = containedPicture(imageRef.current, viewport);
      frame = requestAnimationFrame(() => flushSync(() => setGhost({ from: origin, to })));
    }
    return () => {
      cancelAnimationFrame(frame);
      if (dialog?.open) dialog.close();
      restoreTo?.focus(); // back to the thumbnail that opened the viewer
    };
    // Runs once per opening.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Neighbours are fetched ahead, so a step usually swaps at once.
  useEffect(() => {
    for (const i of [index - 1, index + 1]) {
      if (i >= 0 && i <= last) new Image().src = frameUrl(jobId, i);
    }
  }, [index, last, jobId]);

  function step(delta: number) {
    safe.run(() => {
      const next = Math.min(last, Math.max(0, index + delta));
      if (next === index) return false; // already at the first/last frame: nothing changed
      setIndex(next);
    });
  }

  function onKeyDown(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      if (!event.repeat) step(event.key === "ArrowRight" ? 1 : -1);
      return;
    }
    // A held Enter on Previous/Next acts once, like a held arrow key.
    blockKeyRepeat(event);
    // Keep Tab inside the dialog (wrap from last control to first and back).
    if (event.key === "Tab") {
      const focusables = Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>("button, [href], [tabindex]:not([tabindex='-1'])") ?? [],
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const end = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        end.focus();
      } else if (!event.shiftKey && document.activeElement === end) {
        event.preventDefault();
        first.focus();
      }
    }
  }

  // Escape fires "cancel": close through the same path as the Close button.
  function onCancel(event: SyntheticEvent<HTMLDialogElement>) {
    event.preventDefault();
    finish();
  }

  const label = labels.get(index);
  const shownLabel = labels.get(shownIndex);
  const counter = `frame ${index} of ${frameCount} (t = ${frameTime(index, fps)} s)`;

  return (
    <dialog
      ref={dialogRef}
      className={styles.dialog}
      data-instant={keyboard ? "" : undefined}
      aria-labelledby="frame-dialog-title"
      aria-describedby="frame-dialog-help"
      onKeyDown={onKeyDown}
      onCancel={onCancel}
      // Chrome closes on a repeated Escape even when "cancel" was cancelled:
      // finish at once; focus is still restored on unmount.
      onClose={finish}
    >
      <motion.div
        {...PAGE_CLOCK}
        className={styles.panel}
        // A plain fade in: scaling the panel would move a large area of dense text (MOTION.md principle 3).
        // None when the keyboard opened it: focus is on "Next frame" from the first frame.
        initial={keyboard ? false : m.from({ opacity: 0 })}
        animate={{ opacity: 1 }}
        transition={m.tween(DUR.quick)}
        // The step limiter's wait starts again when the viewer is fully shown (see the SAFETY note above).
        onAnimationComplete={safe.mark}
      >
        <div className={styles.dialogHead}>
          <h2 id="frame-dialog-title">Frame viewer</h2>
          <button type="button" className="btn btn-small" onClick={finish}>
            Close
          </button>
        </div>
        <p className="hint" id="frame-dialog-help">
          Step one frame at a time with the buttons or the Left and Right arrow keys. Steps are limited to 2 per second
          so the viewer itself never flashes. Frames are numbered from 0.
        </p>
        {/* The picture rides the panel's fade (no fade of its own, no delay), so it
            changes on the same frames as the panel and the veil. Decoded with the
            paint: never an empty box between two frames (MOTION.md 3.6). */}
        <img
          ref={imageRef}
          className={styles.dialogImage}
          src={shownSrc}
          alt={`Captured frame ${shownIndex} of ${frameCount}${shownLabel ? `: ${shownLabel}` : ""}`}
          width={viewport.width}
          height={viewport.height}
          decoding="sync"
        />
        <p className={styles.counter} aria-live="polite">
          {counter}
          {label ? ` · ${label}` : ""}
        </p>
        <div className="btn-row">
          <button type="button" className="btn" aria-disabled={index === 0} onClick={() => step(-1)}>
            <span aria-hidden="true">←</span> Previous frame
          </button>
          <button ref={nextRef} type="button" className="btn" aria-disabled={index === last} onClick={() => step(1)}>
            Next frame <span aria-hidden="true">→</span>
          </button>
        </div>
      </motion.div>
      {ghost && (
        <FrameGhost
          className={styles.ghost}
          from={ghost.from}
          to={ghost.to}
          onDone={() => flushSync(() => setGhost(null))}
        />
      )}
    </dialog>
  );
}
