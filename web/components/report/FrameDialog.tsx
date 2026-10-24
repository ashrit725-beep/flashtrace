"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { frameUrl, type Size } from "@/lib/api";
import { frameTime } from "@/lib/format";
import { blockKeyRepeat, useSafeToggle } from "@/lib/useSafeToggle";
import styles from "./Report.module.css";

// SAFETY: the two states of a flashing element are often a white frame and a
// black frame. Holding an arrow key or Enter (auto-repeat, ~30 per second) or
// clicking fast would turn this viewer into a strobe. So steps are manual, key
// repeats are ignored, and useSafeToggle drops steps closer than 350 ms apart:
// the viewer never changes frame more than about 3 times a second.

interface Props {
  jobId: string;
  startIndex: number;
  frameCount: number;
  fps: number;
  viewport: Size;
  /** Labels of the evidence frames, shown when the viewer reaches one. */
  labels: Map<number, string>;
  onClose: () => void;
}

// Mounted only while open (the parent renders it conditionally), so every
// opening starts fresh at startIndex and unmounting restores focus.
export default function FrameDialog({ jobId, startIndex, frameCount, fps, viewport, labels, onClose }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const nextRef = useRef<HTMLButtonElement>(null);
  const safe = useSafeToggle();
  const [index, setIndex] = useState(startIndex);
  const last = Math.max(0, frameCount - 1);

  useEffect(() => {
    const dialog = dialogRef.current;
    const opener = document.activeElement as HTMLElement | null;
    if (dialog && !dialog.open) dialog.showModal(); // modal: the page behind becomes inert
    nextRef.current?.focus();
    return () => {
      if (dialog?.open) dialog.close();
      opener?.focus(); // back to the thumbnail that opened the viewer
    };
  }, []);

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

  const label = labels.get(index);
  const counter = `frame ${index} of ${frameCount} (t = ${frameTime(index, fps)} s)`;

  return (
    // Escape fires the native "cancel" then "close" events; onClose syncs React state.
    <dialog
      ref={dialogRef}
      className={styles.dialog}
      aria-labelledby="frame-dialog-title"
      aria-describedby="frame-dialog-help"
      onKeyDown={onKeyDown}
      onClose={onClose}
    >
      <div className={styles.dialogHead}>
        <h2 id="frame-dialog-title">Frame viewer</h2>
        <button type="button" className="btn btn-small" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="hint" id="frame-dialog-help">
        Step one frame at a time with the buttons or the Left and Right arrow keys. Steps are limited to about 3 per
        second so the viewer itself never flashes. Frames are numbered from 0.
      </p>
      <img
        className={styles.dialogImage}
        src={frameUrl(jobId, index)}
        alt={`Captured frame ${index} of ${frameCount}${label ? `: ${label}` : ""}`}
        width={viewport.width}
        height={viewport.height}
      />
      <p className={styles.counter} aria-live="polite">
        {counter}
        {label ? ` · ${label}` : ""}
      </p>
      <div className="btn-row">
        <button
          type="button"
          className="btn"
          aria-disabled={index === 0}
          onClick={() => step(-1)}
        >
          <span aria-hidden="true">←</span> Previous frame
        </button>
        <button
          ref={nextRef}
          type="button"
          className="btn"
          aria-disabled={index === last}
          onClick={() => step(1)}
        >
          Next frame <span aria-hidden="true">→</span>
        </button>
      </div>
    </dialog>
  );
}
