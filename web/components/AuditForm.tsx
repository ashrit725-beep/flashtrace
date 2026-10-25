"use client";

import { motion } from "motion/react";
import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent, type ReactNode } from "react";
import { createJob, type AttributeMode, type JobSettings, type Size } from "@/lib/api";
import { DUR, PAGE_CLOCK, SHIFT, useMotionTokens } from "@/lib/motion";
import CrossfadeLabel from "./motion/CrossfadeLabel";
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

/**
 * The message under a field (web/MOTION.md 3.1). Its first line sits in a
 * slot that is always reserved, so a one-line message moves nothing below it
 * (the next field stays where the pointer is). It fades in with a 4 px drop
 * from the field just left; extra lines appear at once (no height tween).
 */
function FieldMessage({ id, children }: { id: string; children?: ReactNode }) {
  const m = useMotionTokens();
  return (
    <span className="field-msg">
      {children && (
        <motion.span
          {...PAGE_CLOCK}
          key={id}
          className="error-text"
          id={id}
          initial={m.from({ opacity: 0, y: -SHIFT.xs })}
          animate={{ opacity: 1, y: 0 }}
          transition={m.tween(DUR.quick)}
        >
          {children}
        </motion.span>
      )}
    </span>
  );
}

/**
 * Whether a radio group's next selection mark appears without a fade: on the
 * first render (the default choice was always there) and when an arrow key
 * moved the selection (keyboard actions are instant, MOTION.md principle 3).
 */
function useChoiceMotion() {
  const lastKey = useRef(Number.NEGATIVE_INFINITY);
  const [instant, setInstant] = useState(true);
  return {
    instant,
    onKeyDown: () => {
      lastKey.current = performance.now();
    },
    /** Call from the radio's onChange. */
    note: () => setInstant(performance.now() - lastKey.current < 250),
  };
}

interface CardProps {
  name: string;
  id: string;
  value: string;
  checked: boolean;
  onChange: () => void;
  label: string;
  hint?: string;
  instant: boolean;
}

/**
 * A radio choice as a card. The native radio stays (keyboard, forms, screen
 * readers); the card is its label. The selection ring and tint appear on the
 * checked card in place: the old one goes at once and the new one fades in
 * (160 ms), which says "this one is chosen" without sweeping across the cards
 * between them (a glide showed the middle card as selected on its way past,
 * and stretched the ring between cards of different heights). The keyboard
 * focus ring sits on the card, static.
 */
function RadioCard({ name, id, value, checked, onChange, label, hint, instant }: CardProps) {
  const m = useMotionTokens();
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <label className="radio-card" htmlFor={id}>
      {checked && (
        <motion.span
          {...PAGE_CLOCK}
          className="radio-card-indicator"
          aria-hidden="true"
          initial={instant ? false : m.from({ opacity: 0 })}
          animate={{ opacity: 1 }}
          transition={m.tween(DUR.quick)}
        />
      )}
      <input
        type="radio"
        id={id}
        name={name}
        value={value}
        checked={checked}
        onChange={onChange}
        aria-describedby={hintId}
      />
      <span className="radio-card-text">
        <span className="radio-card-label">{label}</span>
        {hint && (
          <span className="hint" id={hintId}>
            {hint}
          </span>
        )}
      </span>
    </label>
  );
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

  const viewportKeys = useChoiceMotion();
  const attributeKeys = useChoiceMotion();
  // SAFETY and layout: the custom-size row is always there, so choosing a
  // size never moves the blocks below it. Its inputs are enabled only for
  // Custom size and otherwise show the chosen preset's size. Inserting the
  // row pushed everything below down by ~80 px (110 px at 390 px), and churning
  // through the radios at phone width completed flashes on several frames per
  // toggle (self-audit F5 at 390 px: 8 a second); it also moved the attribution
  // cards under a pointer that was about to press one.
  const custom = values.preset === "custom";
  const presetSize = PRESETS.find((p) => p.id === values.preset)?.size;

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
        <FieldMessage id="target-error">{shown("target")}</FieldMessage>
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
        <FieldMessage id="duration-error">{shown("duration")}</FieldMessage>
      </div>

      <fieldset aria-describedby="viewport-hint" onKeyDown={viewportKeys.onKeyDown}>
        <legend className="legend">Viewport</legend>
        <span className="hint" id="viewport-hint">
          Other widths scale the 341 × 256 px window by width (an assumption, listed in the report).
        </span>
        <div className="radio-cards">
          {[...PRESETS.map((p) => ({ id: p.id, label: p.label })), { id: "custom", label: "Custom size" }].map((p) => (
            <RadioCard
              key={p.id}
              name="viewport"
              id={`vp-${p.id}`}
              value={p.id}
              checked={values.preset === p.id}
              onChange={() => {
                viewportKeys.note();
                setValues((prev) => ({ ...prev, preset: p.id }));
              }}
              label={p.label}
              instant={viewportKeys.instant}
            />
          ))}
        </div>
        <div className={styles.sizeRow}>
          <span className={`hint ${styles.sizeHint}`} id="size-hint">
            The chosen size, in pixels. Choose Custom size to type your own.
          </span>
          <div>
            <label htmlFor="vp-width">Width (px)</label>
            <input
              id="vp-width"
              className="input input-short"
              type="number"
              min={200}
              max={3840}
              inputMode="numeric"
              disabled={!custom}
              aria-invalid={shown("width") ? true : undefined}
              aria-describedby={describedBy("width", "size-hint")}
              value={custom ? values.width : String(presetSize?.width ?? "")}
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
              disabled={!custom}
              aria-invalid={shown("height") ? true : undefined}
              aria-describedby={describedBy("height", "size-hint")}
              value={custom ? values.height : String(presetSize?.height ?? "")}
              onChange={set("height")}
              onBlur={blur("height")}
            />
          </div>
          <div className={styles.sizeMessages}>
            <FieldMessage id="width-error">{shown("width")}</FieldMessage>
            {shown("height") && <FieldMessage id="height-error">{shown("height")}</FieldMessage>}
          </div>
        </div>
      </fieldset>

      <fieldset onKeyDown={attributeKeys.onKeyDown}>
        <legend className="legend">Find the cause (attribution)</legend>
        <div className="radio-cards radio-cards-wide">
          {MODES.map((mode) => (
            <RadioCard
              key={mode.id}
              name="attribute"
              id={`attr-${mode.id}`}
              value={mode.id}
              checked={attribute === mode.id}
              onChange={() => {
                attributeKeys.note();
                setAttribute(mode.id);
              }}
              label={mode.label}
              hint={mode.hint}
              instant={attributeKeys.instant}
            />
          ))}
        </div>
      </fieldset>

      <div>
        {submitError !== null && (
          <div className={styles.submitError}>
            <ErrorState title="The audit could not be started" error={submitError} onRetry={submit} focusOnMount />
          </div>
        )}

        {/* aria-disabled, not disabled: a disabled button drops keyboard focus
            to <body>, so after a failed submit the user would have to Tab back
            from the top of the page. submit() ignores presses while busy. The
            label crossfades inside a fixed minimum width, so nothing jumps. */}
        <button type="submit" className={`btn btn-primary ${styles.runButton}`} aria-disabled={submitting}>
          <CrossfadeLabel value={submitting ? "starting" : "run"}>{submitting ? "Starting…" : "Run audit"}</CrossfadeLabel>
        </button>
      </div>
    </form>
  );
}
