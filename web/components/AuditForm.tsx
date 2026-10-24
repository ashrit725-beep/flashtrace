"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { createJob, type AttributeMode, type JobSettings, type Size } from "@/lib/api";
import { ErrorState } from "./StatusViews";
import styles from "./NewAudit.module.css";

// The viewport presets offered by the form. 1024 x 768 is the default
// because WCAG's 341 x 256 px "small safe area" assumes a 1024 x 768 screen.
const PRESETS: { id: string; size: Size; label: string }[] = [
  { id: "1024x768", size: { width: 1024, height: 768 }, label: "1024 × 768 (WCAG reference)" },
  { id: "1366x768", size: { width: 1366, height: 768 }, label: "1366 × 768 (laptop)" },
  { id: "1920x1080", size: { width: 1920, height: 1080 }, label: "1920 × 1080 (desktop)" },
  { id: "375x812", size: { width: 375, height: 812 }, label: "375 × 812 (phone)" },
];

const MODES: { id: AttributeMode; label: string; hint: string }[] = [
  { id: "none", label: "None", hint: "Verdict only. No element is named as the cause." },
  {
    id: "bbox",
    label: "Bounding boxes (recommended)",
    hint: "Matches the flashing area to page elements by their on-screen boxes. One capture.",
  },
  {
    id: "ablate",
    label: "Ablation",
    hint: "Also hides each suspect element and measures again to confirm the cause. Slower: re-captures the page up to 6 times.",
  },
];

/**
 * A web address typed without its scheme: "example.com", "www.example.com/a",
 * "127.0.0.1:8765/site/", "localhost:3000". The server would read it as a
 * local path and answer 422, so say what is wrong here instead. A file name
 * such as "index.html" or "demo/site/index.html" is NOT caught: relative paths
 * inside the FlashTrace folder are valid targets (docs/API.md), and the
 * server's own 422 message covers every other case.
 */
const BARE_HOST = /^(localhost|[\w-]+(\.[\w-]+)+)(:\d+)?(\/|$)/i;
const FILE_NAME = /\.(x?html?|svg)$/i;

function looksLikeBareAddress(target: string): boolean {
  const host = target.split("/")[0];
  return BARE_HOST.test(target) && !FILE_NAME.test(host);
}

type Field = "target" | "duration" | "width" | "height";
type Errors = Partial<Record<Field, string>>;

/** The id of the input for each field, used to move focus to the first error. */
const INPUT_ID: Record<Field, string> = { target: "target", duration: "duration", width: "vp-width", height: "vp-height" };

interface FormValues {
  target: string;
  duration: string;
  preset: string;
  width: string;
  height: string;
}

/** Pure validation, so the same rules run on blur and on submit. */
function validate(v: FormValues): Errors {
  const errors: Errors = {};
  const target = v.target.trim();
  if (!target) errors.target = "Enter a URL or a local file path.";
  else if (looksLikeBareAddress(target))
    errors.target = `This looks like a web address. Add https:// at the start (https://${target}), or for a file use a path such as demo/site/index.html.`;

  const duration = Number(v.duration);
  if (v.duration.trim() === "" || !Number.isFinite(duration) || duration < 1 || duration > 60)
    errors.duration = "Enter a number of seconds from 1 to 60.";

  if (v.preset === "custom") {
    for (const side of ["width", "height"] as const) {
      const n = Number(v[side]);
      if (!Number.isInteger(n) || n < 200 || n > 3840)
        errors[side] = `${side === "width" ? "Width" : "Height"} must be a whole number from 200 to 3840 px.`;
    }
  }
  return errors;
}

/** Turns the form values into the POST /jobs body. */
function toSettings(v: FormValues, attribute: AttributeMode): JobSettings {
  const preset = PRESETS.find((p) => p.id === v.preset);
  const viewport = preset ? preset.size : { width: Number(v.width), height: Number(v.height) };
  return { duration: Number(v.duration), viewport, attribute };
}

export default function AuditForm() {
  const router = useRouter();
  const [values, setValues] = useState<FormValues>({
    target: "",
    duration: "10",
    preset: "1024x768",
    width: "1280",
    height: "800",
  });
  const [attribute, setAttribute] = useState<AttributeMode>("bbox");
  // Errors are shown only for fields the user has left (or after submit),
  // so the form does not shout at someone who has not typed yet.
  const [touched, setTouched] = useState<Partial<Record<Field, boolean>>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<unknown>(null);

  const errors = validate(values);
  const shown = (field: Field) => (touched[field] ? errors[field] : undefined);
  const set = (field: keyof FormValues) => (e: { target: { value: string } }) =>
    setValues((prev) => ({ ...prev, [field]: e.target.value }));
  const blur = (field: Field) => () => setTouched((prev) => ({ ...prev, [field]: true }));

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void submit();
  }

  // Shared by the form and the error box's Retry, which sends the same values again.
  async function submit() {
    if (submitting) return; // the button is only aria-disabled
    setTouched({ target: true, duration: true, width: true, height: true });
    const order: Field[] = ["target", "duration", "width", "height"];
    const firstBad = order.find((f) => errors[f]);
    if (firstBad) {
      // Move focus to the first problem; its message is read out through
      // aria-describedby.
      document.getElementById(INPUT_ID[firstBad])?.focus();
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      const settings = toSettings(values, attribute);
      const { id } = await createJob({ url: values.target.trim(), ...settings });
      router.push(`/run/?id=${encodeURIComponent(id)}`);
    } catch (error) {
      setSubmitError(error);
      setSubmitting(false);
    }
  }

  // aria-describedby lists the hint and, when present, the error message.
  const describedBy = (field: Field, hintId?: string) =>
    [hintId, shown(field) ? `${field}-error` : undefined].filter(Boolean).join(" ") || undefined;

  return (
    <form onSubmit={onSubmit} noValidate aria-labelledby="form-heading">
      <h2 id="form-heading" className="visually-hidden">
        Audit settings
      </h2>

      <div className="field">
        <label htmlFor="target">Page URL or local file path</label>
        <span className="hint" id="target-hint">
          For example https://example.com/ or demo/site/index.html (a path inside the FlashTrace folder)
        </span>
        <input
          id="target"
          name="target"
          className="input"
          type="text"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          required
          aria-required="true"
          aria-invalid={shown("target") ? true : undefined}
          aria-describedby={describedBy("target", "target-hint")}
          value={values.target}
          onChange={set("target")}
          onBlur={blur("target")}
        />
        {shown("target") && (
          <span className="error-text" id="target-error">
            {shown("target")}
          </span>
        )}
      </div>

      <div className="field">
        <label htmlFor="duration">Duration (seconds)</label>
        <span className="hint" id="duration-hint">
          How long to record the page, 1 to 60 seconds.
        </span>
        <input
          id="duration"
          name="duration"
          className="input input-short"
          type="number"
          min={1}
          max={60}
          step={1}
          inputMode="numeric"
          aria-invalid={shown("duration") ? true : undefined}
          aria-describedby={describedBy("duration", "duration-hint")}
          value={values.duration}
          onChange={set("duration")}
          onBlur={blur("duration")}
        />
        {shown("duration") && (
          <span className="error-text" id="duration-error">
            {shown("duration")}
          </span>
        )}
      </div>

      <fieldset aria-describedby="viewport-hint">
        <legend className="legend">Viewport</legend>
        <span className="hint" id="viewport-hint">
          Other widths scale the 341 × 256 px window by width (an assumption, listed in the report).
        </span>
        {PRESETS.map((p) => (
          <div className="choice" key={p.id}>
            <input
              type="radio"
              id={`vp-${p.id}`}
              name="viewport"
              value={p.id}
              checked={values.preset === p.id}
              onChange={set("preset")}
            />
            <label htmlFor={`vp-${p.id}`}>{p.label}</label>
          </div>
        ))}
        <div className="choice">
          <input
            type="radio"
            id="vp-custom"
            name="viewport"
            value="custom"
            checked={values.preset === "custom"}
            onChange={set("preset")}
          />
          <label htmlFor="vp-custom">Custom size</label>
        </div>
        {values.preset === "custom" && (
          <div className={styles.sizeRow}>
            <div>
              <label htmlFor="vp-width">Width (px)</label>
              <input
                id="vp-width"
                className="input input-short"
                type="number"
                min={200}
                max={3840}
                inputMode="numeric"
                aria-invalid={shown("width") ? true : undefined}
                aria-describedby={describedBy("width")}
                value={values.width}
                onChange={set("width")}
                onBlur={blur("width")}
              />
            </div>
            <span className={styles.times} aria-hidden="true">
              ×
            </span>
            <div>
              <label htmlFor="vp-height">Height (px)</label>
              <input
                id="vp-height"
                className="input input-short"
                type="number"
                min={200}
                max={3840}
                inputMode="numeric"
                aria-invalid={shown("height") ? true : undefined}
                aria-describedby={describedBy("height")}
                value={values.height}
                onChange={set("height")}
                onBlur={blur("height")}
              />
            </div>
            {shown("width") && (
              <span className="error-text" id="width-error">
                {shown("width")}
              </span>
            )}
            {shown("height") && (
              <span className="error-text" id="height-error">
                {shown("height")}
              </span>
            )}
          </div>
        )}
      </fieldset>

      <fieldset>
        <legend className="legend">Find the cause (attribution)</legend>
        {MODES.map((m) => (
          <div className="choice" key={m.id}>
            <input
              type="radio"
              id={`attr-${m.id}`}
              name="attribute"
              value={m.id}
              checked={attribute === m.id}
              onChange={() => setAttribute(m.id)}
              aria-describedby={`attr-${m.id}-hint`}
            />
            <label htmlFor={`attr-${m.id}`}>{m.label}</label>
            <span className="hint" id={`attr-${m.id}-hint`}>
              {m.hint}
            </span>
          </div>
        ))}
      </fieldset>

      {submitError !== null && (
        <div className={styles.submitError}>
          <ErrorState title="The audit could not be started" error={submitError} onRetry={submit} focusOnMount />
        </div>
      )}

      {/* aria-disabled, not disabled: a disabled button drops keyboard focus
          to <body>, so after a failed submit the user would have to Tab back
          from the top of the page. submit() ignores presses while busy. */}
      <button type="submit" className="btn btn-primary" aria-disabled={submitting}>
        {submitting ? "Starting…" : "Run audit"}
      </button>
    </form>
  );
}
