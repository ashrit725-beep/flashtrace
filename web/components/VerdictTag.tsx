import type { Verdict } from "@/lib/api";

// Word + glyph + colour: the verdict must never rely on colour alone. The
// glyph is decorative (aria-hidden) because the word already says it.
export const VERDICT_GLYPH: Record<Verdict, string> = {
  PASS: "✓",
  FAIL: "✗",
  INCONCLUSIVE: "?",
};

export default function VerdictTag({ verdict, pending = true }: { verdict: Verdict | null; pending?: boolean }) {
  if (!verdict) return <span className="muted">{pending ? "No verdict yet" : "No verdict"}</span>;
  return (
    <span className={`verdict verdict-${verdict}`}>
      <span className="verdict-glyph" aria-hidden="true">
        {VERDICT_GLYPH[verdict]}
      </span>
      {verdict}
    </span>
  );
}
