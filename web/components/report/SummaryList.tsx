import type { Report } from "@/lib/api";
import { RED_RULE_LABEL, STRATEGY_LABEL, formatDateTime, sizeLabel } from "@/lib/format";
import styles from "./Report.module.css";

// The conditions the verdict holds under. A verdict is only meaningful
// together with these, so they sit directly under the banner.
export default function SummaryList({ report }: { report: Report }) {
  const { settings, capture } = report;
  const items: { term: string; value: string; wide?: boolean; mono?: boolean }[] = [
    { term: "Page", value: report.url, wide: true, mono: true },
    { term: "Captured at", value: formatDateTime(report.capturedAt) },
    { term: "Capture strategy", value: STRATEGY_LABEL[capture.strategy] ?? capture.strategy },
    { term: "Deterministic", value: capture.deterministic ? "Yes" : "No" },
    {
      term: "Frames",
      value: `${capture.frameCount} at ${settings.fps} fps (${settings.duration} s)`,
    },
    { term: "Red flash rule", value: RED_RULE_LABEL[settings.redRule] ?? settings.redRule },
    { term: "Window size", value: `${sizeLabel(settings.windowPx)} px` },
    { term: "Viewport", value: `${sizeLabel(settings.viewport)} px` },
    { term: "Engine", value: `version ${report.engineVersion}` },
  ];
  return (
    <dl className={styles.summary}>
      {items.map((item) => (
        <div key={item.term} className={item.wide ? styles.summaryWide : undefined}>
          <dt>{item.term}</dt>
          <dd className={item.mono ? "url" : undefined}>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
