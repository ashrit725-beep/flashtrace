import { frameUrl, type EvidenceFrame, type Size } from "@/lib/api";
import { frameTime } from "@/lib/format";
import styles from "./Report.module.css";

interface Props {
  jobId: string;
  frames: EvidenceFrame[];
  fps: number;
  viewport: Size;
  onOpen: (index: number) => void;
}

// Evidence is still frames only (docs/SPEC.md section 9). Each thumbnail is a
// real <button>, so Enter/Space open the viewer as well as a click.
export default function EvidenceStrip({ jobId, frames, fps, viewport, onOpen }: Props) {
  if (frames.length === 0) {
    return <p className="muted">No evidence frames were saved for this audit.</p>;
  }
  return (
    <ul className={styles.evidence}>
      {frames.map((frame) => (
        <li key={frame.index}>
          <button
            type="button"
            className={styles.thumb}
            onClick={() => onOpen(frame.index)}
          >
            {/* The name is the visible text plus this prefix, so speech
                users can say what they see (WCAG 2.5.3 Label in Name). */}
            <span className="visually-hidden">Enlarge: </span>
            <img
              src={frameUrl(jobId, frame.index)}
              alt=""
              width={viewport.width}
              height={viewport.height}
              loading="lazy"
              decoding="async"
            />
            <span className={styles.thumbLabel}>{frame.label}</span>
            <span className={styles.thumbMeta}>
              frame {frame.index} · t = {frameTime(frame.index, fps)} s
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
