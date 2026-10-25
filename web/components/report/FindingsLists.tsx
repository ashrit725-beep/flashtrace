import type { Report } from "@/lib/api";
import { frameTime, sizeLabel } from "@/lib/format";
import { isKeyboardClick } from "@/lib/motion";
import type { OpenRequest } from "./EvidenceStrip";
import styles from "./Report.module.css";

interface Props {
  report: Report;
  /** Opens the frame viewer. A text button has no thumbnail, so the viewer just fades in (no ghost). */
  onViewFrame: (request: OpenRequest) => void;
}

export function ViolationsList({ report, onViewFrame }: Props) {
  const { violations, settings } = report;
  if (violations.length === 0) {
    // The calm empty state: say exactly what was and was not checked.
    return (
      <p>
        No violations. In the captured {settings.duration} s at {sizeLabel(settings.viewport)} px, no one-second
        window had more than three qualifying general or red flashes. That is a finding about these conditions only,
        not a guarantee for other screens, durations or interactions.
      </p>
    );
  }
  const fps = settings.fps;
  return (
    <ol className={styles.findings}>
      {violations.map((v, i) => {
        const first = v.frameIndices.length ? v.frameIndices[0] : v.start;
        return (
          <li key={`${v.type}-${v.start}`}>
            <strong>
              Violation {i + 1}: {v.type} flashes, up to {v.maxRatePerSec} per second
            </strong>
            <br />
            <span className="num">
              Frames {v.start}–{v.end} (t = {frameTime(v.start, fps)}–{frameTime(v.end, fps)} s),{" "}
              {v.frameIndices.length} qualifying flash{v.frameIndices.length === 1 ? "" : "es"}, worst window at x{" "}
              {v.window.x}, y {v.window.y} ({v.window.w} × {v.window.h} px).
            </span>{" "}
            <button
              type="button"
              className="btn btn-small"
              onClick={(event) =>
                onViewFrame({ index: first, origin: null, keyboard: isKeyboardClick(event), opener: event.currentTarget })
              }
            >
              View frame {first}
            </button>
          </li>
        );
      })}
    </ol>
  );
}

export function AdvisoryList({ report }: { report: Report }) {
  if (report.advisory.length === 0) return <p>No advisories.</p>;
  const fps = report.settings.fps;
  return (
    <ul className={styles.findings}>
      {report.advisory.map((a) => (
        <li key={`${a.flashType}-${a.start}`}>
          <strong>Sustained {a.flashType} flashing</strong>, about {a.meanRatePerSec.toFixed(1)} per second from t ={" "}
          {frameTime(a.start, fps)} s to {frameTime(a.end, fps)} s.{" "}
          <span className="badge badge-no">{a.label}</span>
        </li>
      ))}
    </ul>
  );
}

export function TextList({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) return <p>{empty}</p>;
  return (
    <ul className={styles.findings}>
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}
