"use client";

import type { MouseEvent } from "react";
import { frameUrl, type EvidenceFrame, type Size } from "@/lib/api";
import { frameTime } from "@/lib/format";
import { isKeyboardClick } from "@/lib/motion";
import { boxOf, type Box } from "../motion/FrameGhost";
import styles from "./Report.module.css";

/** What the frame viewer needs to know about how it was opened. */
export interface OpenRequest {
  index: number;
  /** The thumbnail image's box for a pointer click (the ghost grows from it); null from the keyboard or a text button. */
  origin: Box | null;
  /** True when the keyboard opened it (a click with detail 0): the viewer appears at once. */
  keyboard: boolean;
  opener: HTMLElement | null;
}

interface Props {
  jobId: string;
  frames: EvidenceFrame[];
  fps: number;
  viewport: Size;
  onOpen: (request: OpenRequest) => void;
}

function Thumb({ jobId, frame, fps, viewport, onOpen }: { jobId: string; frame: EvidenceFrame; fps: number; viewport: Size; onOpen: Props["onOpen"] }) {
  function open(event: MouseEvent<HTMLButtonElement>) {
    const img = event.currentTarget.querySelector("img");
    const keyboard = isKeyboardClick(event);
    onOpen({
      index: frame.index,
      origin: keyboard || !img ? null : boxOf(img),
      keyboard,
      opener: event.currentTarget,
    });
  }
  return (
    <button type="button" className={styles.thumb} onClick={open}>
      {/* The name is the visible text plus this prefix, so speech
          users can say what they see (WCAG 2.5.3 Label in Name). */}
      <span className="visually-hidden">Enlarge: </span>
      <img
        src={frameUrl(jobId, frame.index)}
        alt=""
        width={viewport.width}
        height={viewport.height}
        // Eager and decoded with the paint (MOTION.md 3.6): a card never shows an
        // empty box for a frame while its picture decodes. There are only a few small stills.
        loading="eager"
        decoding="sync"
      />
      <span className={styles.thumbLabel}>{frame.label}</span>
      <span className={styles.thumbMeta}>
        frame {frame.index} · t = {frameTime(frame.index, fps)} s
      </span>
    </button>
  );
}

// Evidence is still frames only (docs/SPEC.md section 9). Each thumbnail is a
// real <button>, so Enter/Space open the viewer as well as a click.
// Motion (web/MOTION.md 3.3, section 8): none on arrival. The frames are
// static evidence and were there all along; a scroll-triggered stagger told
// the user nothing (and made two staggered lists on one screen). The one
// deliberate motion here is the ghost that grows from a thumbnail into the
// frame viewer.
export default function EvidenceStrip({ jobId, frames, fps, viewport, onOpen }: Props) {
  if (frames.length === 0) {
    return <p className="muted">No evidence frames were saved for this audit.</p>;
  }
  return (
    <ul className={styles.evidence}>
      {frames.map((frame) => (
        <li key={frame.index}>
          <Thumb jobId={jobId} frame={frame} fps={fps} viewport={viewport} onOpen={onOpen} />
        </li>
      ))}
    </ul>
  );
}
