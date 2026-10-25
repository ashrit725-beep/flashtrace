# FlashTrace web viewer: motion and look-and-feel spec

Design source for the motion work allowed by docs/DECISIONS.md ADR-024, within docs/SPEC.md section 9. Where the
user's skills conflict with the safety rule, the safety rule wins. Example: emil-design-eng prefers "gentler, not
zero" under reduced motion, but FlashTrace uses zero. Never output the forbidden compliance phrase. The footer
disclaimer stays on every screen.

Critic pass (2026-10-24): a photosensitivity and accessibility review (WCAG 2.3.1, 2.2.2, 2.3.3) plus a motion-design
review against design-motion-principles, emil-design-eng, make-interfaces-feel-better and antislop-ui. Every change is
marked **Critic:**. Section 3.6 lists the risk-free improvements that were missing.

## 1. Principles

1. **Causal or nothing.** Every movement answers "where did this come from?" or "what changed?". If the cause cannot be named, the element stays still.
2. **Never periodic.** No infinite, looping, repeating or ambient animation. No shimmer, spinners, pulses, marquees or animated gradients. Every animation is one-shot and lasts 600 ms or less. **Critic:** a choreography (delays and staggers included) also ends within 600 ms of its trigger. Poll-driven updates move only when the data changes, never between polls. No scroll-linked (scrubbed) animation: scrolling up and down can replay it at any speed, so it cannot be gated.
3. **Calm.** Use small distances (4 to 8 px), no overshoot (spring bounce is always 0), and ease-out curves. Exits are faster than entrances. Keyboard actions are instant. **Critic:** concretely, keyboard-initiated *movement* (glides, slides, the dialog ghost) is instant; detect it with `event.detail === 0` on the click. A large-area opacity crossfade keeps its short fade for keyboard users too, because the fade softens a luminance change and never delays use. Large-area *movement* is not used at all (section 6).
4. **Gated reversals.** Any user control that can flip a large area goes through a rate gate (`useSafeToggle`, 350 ms or more). Throttle the consequence, not the input, where possible. **Critic:** new gates (`useThrottledValue`, `useSafeNavigate`, the dialog open gate) are leading plus trailing: the first change applies at once, later ones inside the window collapse into one trailing change (final intent wins), so a single click never waits. The existing drop-style `useSafeToggle` stays for frame steps and toggles. Controls whose consequences overlap share one gate. Browser history traversal bypasses every control, so the route display gate (3.0) covers it. Gates and holds stay on under reduced motion: they are safety, not motion. Hover cannot be gated, so hover colours stay below the transition threshold (section 6).
5. **Evidence is exact.** Captured frames, the heatmap and measurements are never tweened into states that did not exist. Frame stepping stays instant. **Critic:** concretely, evidence images never move, scale or blur (the frame viewer opens with a ghost outline, 3.3), and a number is never shown at a value it did not have: no count-up from 0 and no Before→After ticking. The one count animation left is the Running frame counter, whose in-between values were really captured. Chart marks may draw once from the baseline, because a mark being drawn is not read as a value; the data table and the readout give the exact values.
6. **Reduced motion means none.** Under `prefers-reduced-motion: reduce`, every state change is instant and `document.getAnimations()` is empty. **Critic:** the first painted frame is already the final state: every motion component takes `initial` from `useMotionTokens()`, which returns `false` under reduce (otherwise Motion paints one frame of the hidden `initial` before skipping to the end, and in-view groups stay hidden until scrolled). Motion's JavaScript-driven animations (layout, springs, `x`/`y`, motion values, `pathLength`) are invisible to `getAnimations()`, so the tests also check that requestAnimationFrame goes quiet (section 6).
7. **Never hidden by motion.** Server-rendered chrome and page heads never start hidden. Only content that arrives with data animates in, and it is visible if motion is off. **Critic:** content that waits for an in-view trigger is hidden only after an IntersectionObserver has reported it off screen, is revealed at once when focus moves into it and before printing (`beforeprint` plus an `@media print` override to opacity 1 and no transform), and is never hidden under reduced motion. Keyboard focus never lands on something invisible.

## 2. Tokens

**Dependency:** add `"motion": "12.43.0"` to package.json, an exact pin released 2026-07-28. The v12 API matches the skills. 13.x and 14.x are a separate upgrade decision. Import only from `motion/react`. Never use `framer-motion` or `motion-plus`. **Critic:** the release date could not be checked offline during the critique; confirm it with `npm view motion time` before installing, and keep the pin only if it is at least two weeks old.

```css
/* app/globals.css :root (light and dark share these) */
--dur-press: 120ms;  --dur-quick: 160ms;  --dur-base: 240ms;  --dur-emphasis: 420ms;  --dur-max: 600ms;
--ease-out: cubic-bezier(0.23, 1, 0.32, 1);       /* entrances, responses to input */
--ease-in-out: cubic-bezier(0.77, 0, 0.175, 1);   /* something already on screen moving. Critic: never for large-area opacity, where its steep middle concentrates the luminance change; use --ease-out */
--ease-draw: cubic-bezier(0.65, 0, 0.35, 1);      /* path draws, bars growing */
--stagger: 40ms;  --stagger-max: 240ms;  --shift-xs: 4px;  --shift-s: 8px;  --press-scale: 0.97;  --enter-scale: 0.98;
--transition: var(--dur-quick) var(--ease-out);   /* replaces "150ms ease-out" */
/* @media (prefers-reduced-motion: reduce): every --dur-* = 0ms, --shift-* = 0px, --press-scale = 1, --enter-scale = 1;
   the existing `* { transition: none !important; animation: none !important }` block stays. */
```

```ts
// lib/motion.ts (tokens + hooks; no JSX)
export const DUR = { instant: 0, press: 0.12, quick: 0.16, base: 0.24, emphasis: 0.42, max: 0.6 } as const; // seconds
export const EASE = { out: [0.23, 1, 0.32, 1], inOut: [0.77, 0, 0.175, 1], draw: [0.65, 0, 0.35, 1] } as const;
export const SPRING = { glide: { type: "spring", visualDuration: 0.24, bounce: 0 } } as const; // interruptible moves only
export const STAGGER = { step: 0.04, max: 0.24 } as const;
export const SHIFT = { xs: 4, s: 8 } as const;
export const GATE_MS = { step: MIN_STEP_MS /* 350, lib/useSafeToggle */, largeArea: MIN_STEP_MS, dialogOpen: 1000, routeAnim: 1000, routeDisplay: MIN_STEP_MS, remove: 1000 } as const; // Critic: largeArea, routeDisplay
export const staggerDelay = (i: number, n: number) => Math.min(STAGGER.step, STAGGER.max / Math.max(1, n - 1)) * i;
export function useMotionTokens(); // reduced-aware: returns { duration: 0 } transitions when useReducedMotion() is true. Critic: and initial: false
export function useThrottledValue<T>(value: T, ms = GATE_MS.step): T; // Critic: leading plus trailing: the first change is immediate, the final intent always wins
export function useLargeAreaGate(): SafeToggle; // Critic: one gate per screen for controls whose consequences overlap (report: heatmap overlay + data table)
export const isKeyboardClick = (e: { detail: number }) => e.detail === 0; // Critic: keyboard-activated clicks get no movement
```

`components/MotionRoot.tsx` is a client component wrapped around `{children}` in layout.tsx. It renders
`<MotionConfig reducedMotion="user" transition={reduce ? { duration: 0 } : undefined}>`. It also sets
`MotionGlobalConfig.skipAnimations = reduce` and keeps it in sync with a `matchMedia` listener. `reducedMotion="user"`
alone would keep opacity animations. **Critic:** the first `skipAnimations` assignment runs at module evaluation on the
client, not in an effect: React runs child effects before the parent's, so an effect in MotionRoot would let a child's
first animation start before the flag is set. A unit test runs each `SPRING` through Motion's `spring()` generator until it
is done and asserts it settles in 600 ms or less. Tweens are used everywhere else, so every duration has a hard
upper bound.

## 3. Behaviour per screen and component

"Instant" in the reduced-motion column means no animation: the final state shows on the first frame. Areas are measured at a 1280 x 900 viewport.

### 3.0 App frame and route transitions

**Decision: Motion, enter-only, in `app/template.tsx`. React `<ViewTransition>` is not used.**
- `template.tsx` remounts once per route segment and runs entirely in the browser, so it works in the static export.
- Motion runs on page `requestAnimationFrame` and WAAPI. FlashTrace's capture steps both clocks, so the self-audit is deterministic. A view transition's snapshot and update phases run in rendering steps outside the fake clock, which risks `deterministic=false`. The ADR-024 proof would then be unprovable.
- A view transition hands hit-testing to `::view-transition` for its whole duration, so a fast click is swallowed.
- There is one reduced-motion path instead of two.
- Exit animations would need App Router internals (a frozen router context), so the old page leaves at once.
- First load renders with `initial={false}`, so the exported HTML has no `opacity: 0` and content shows with JS off.
- **Critic:** the first-load flag lives in module scope and is flipped in an effect, never during render (a render-time flip breaks hydration under Strict Mode's double render).
- **Critic:** static-export and remount pitfalls. Every `layoutId` is namespaced by route and component (`report:culprit-outline`, `new:viewport-indicator`) and each page sits in its own `<LayoutGroup id={route}>`: `useId` values repeat across pages, and a matching `layoutId` would glide an element in from the previous page's position. `document.startViewTransition`, React `<ViewTransition>`, `experimental.viewTransition`, CSS `@view-transition` (cross-document: it would fire on full page loads of the export), `animation-timeline` and CSS `scroll-behavior: smooth` stay out: they run on compositor or scroll clocks outside the capture's fake clock.
- **Critic:** route display gate. Back, Forward, a held Alt+Left and the mouse back button change routes without touching any control, at up to about 30 per second. `template.tsx` records the time of each route mount; a route that mounts less than 350 ms (`GATE_MS.routeDisplay`) after the previous one renders at opacity 0 (paper, layout intact, `aria-busy="true"`) and is shown, without an entrance, 350 ms after the last mount of the burst. A burst therefore shows old page, blank, final page, and nothing in between. A `pageshow` event with `persisted` (a back-forward-cache restore of a full document) goes through the same gate. This is safety, not motion, so it also applies under reduced motion.

| Element | Trigger | What moves | Why | Duration / easing | Reduced | Safety note |
|---|---|---|---|---|---|---|
| `<main>` content (template) | client navigation | opacity 0→1 (**Critic:** the y 8→0 slide is dropped: moving a whole page of dense text makes every glyph cross its cells on the same frames, section 6) | arrival at a new place after a click | base 240, ease-out | instant | whole main area. Skipped if the previous animated entrance was under 1000 ms ago. **Critic:** `useSafeNavigate` in NavLinks is leading plus trailing at 350 ms (the first click navigates at once), intercepts only plain primary clicks (modifier and middle clicks keep their new-tab behaviour) and keeps the real `<a href>`. Back and Forward are covered by the route display gate above |
| Active nav underline | route change | 2 px rule glides between items (`layoutId`, persists in the layout) | shows where you went from | `SPRING.glide`. **Critic:** instant when the navigation came from the keyboard | instant | about 80 x 2 px |
| Buttons and links | `:active` / hover | scale 0.97 on press. Background and border colour on hover | the press was heard | press 120 / quick 160, ease-out | colour only, instant | hover is gated by `(hover: hover) and (pointer: fine)`. No scale when `aria-disabled`. **Critic:** the press uses `:active:not(:focus-visible)`, so Space on a keyboard-focused button never scales. Hover colours stay below the transition threshold (sections 4 and 6) |
| Loading states | data pending | nothing | the text says it | none | none | no skeleton shimmer and no spinner (looping). **Critic:** the loading copy appears only after 250 ms, in a reserved block, so a fast local answer never flickers Loading → content |
| Error and empty states | appear | opacity 0→1, y 4→0 | something new needs attention | quick 160, ease-out | instant | `role=alert` is announced at mount, regardless of animation |

### 3.1 New audit (`/`)

| Element | Trigger | What moves | Why | Duration / easing | Reduced | Safety note |
|---|---|---|---|---|---|---|
| Page head, explainer | none | nothing (server-rendered) | | | | never starts hidden |
| Connected line and form | health check answers | opacity 0→1, y 4→0 | the form became usable because the server answered | base 240, ease-out | instant | one-shot |
| Inputs | hover / focus | border colour. Focus ring appears with no transition | focus must be immediate for keyboard users | quick 160 (border only) | instant | |
| Validation message | blur / submit | opacity 0→1, y -4→0 inside a reserved one-line slot (`min-height: 1.25em`, taken from the field margin). Extra lines appear instantly (**Critic:** no height tween: height is a layout property, and a tween only stretches the shift) | links the message to the field just left without moving the next field under the pointer | quick 160, ease-out | instant | no layout shift for one-line messages |
| Viewport and attribution choices | selection | become radio cards (two columns, one on phones). A selection indicator (accent ring as `box-shadow`, `--pass-bg` tint) glides to the checked card (`layoutId` per group via `useId`) | the selection moved from here to there | `SPRING.glide`. Instant when the change came from an arrow key | instant | tint vs surface: ΔL 0.15 but darker side 0.83 > 0.80, so not a flash transition. Ring is 2 px. **Critic:** the keyboard focus ring sits on the card through `:has(input:focus-visible)`, static |
| Custom size row | Custom selected | **Critic:** the row is inserted at full height at once and fades in (opacity 0→1). The blocks below glide down with `layout="position"` (transform only) instead of a height 0→auto tween | the row exists because of that choice | fade quick 160; glide `SPRING.glide` | instant | visibility follows `useThrottledValue(isCustom, 350)`, so radio churn cannot toggle the large shift faster than about 3/s |
| Run audit button | press, then submit | scale 0.97. The label crossfades from "Run audit" to "Starting…" inside a fixed `min-width` | the press was heard and work started | press 120. Label quick 160 | colour only | no width jump. No cross-route morph (see below) |
| Hand-off to Running | navigation | the Running status block enters (route entrance) at the column where the button was | continuity without a fragile morph | as route | instant | a true morph across routes needs a shared tree, which a static-export remount does not have |

### 3.2 Running (`/run/?id=`)

| Element | Trigger | What moves | Why | Duration / easing | Reduced | Safety note |
|---|---|---|---|---|---|---|
| Progress fill | each poll (1 s or more apart) | fill `scaleX` (origin left) tweens to current/total. **Critic:** the native `<progress value max aria-valuetext>` stays as the one accessible element (visually hidden) and the fill is `aria-hidden`. A second `role=progressbar` would be announced twice, and the existing tests read `progress` | the bar moves only because the server reported progress, never in between | emphasis 420, ease-out. Retargets mid-flight | instant | 640 x 14 px. Keyed by phase: a new phase starts at its own value without sliding backwards |
| Frame counter "312/600" | each poll | number ticks from the previous to the new value (`NumberTicker`, follow mode) | progress is counting frames | 400 ms, ease-out | instant | at most 7 digits, about 1,500 px². The animated span is `aria-hidden`; the final value is in visually hidden text. **Critic:** the only count animation in the app, allowed because the frames in between really were captured. No tween when the value is unchanged or the tab is hidden. Fixed width in `ch` with tabular numerals, so nothing beside it moves. WCAG 2.2.2: progress of the user's own job is essential, and each update is a one-shot under 5 s with nothing moving between polls |
| Step complete | phase advances | circle fills (background colour). Check path draws (`pathLength` 0→1, 60 ms delay) | this step finished, because the next one started | base 240, ease-draw | instant | 24 px glyphs. The current step is a static ring with no pulse |
| Completion hand-off | job report-ready | last check draws, bar reaches 100 %, heading crossfades to "Done. Opening the report…", hold 450 ms, then `router.replace` | the end state is seen before the page changes (peak-end) | 450 ms hold (time, not motion) | hold kept, no motion | report entrance follows (3.3). **Critic:** only when this screen showed the job before it was done; a job already done on the first answer redirects at once (no hold, no draws). "Done. Opening the report…" is in a polite live region |
| Cancel and error states | user or server | as 3.0 error and empty states | | | | |

### 3.3 Report (`/report/?id=`)

Entrances run once per job id, on the first data arrival. A report reload (after Verify) never replays them.
Below-the-fold groups use `whileInView` with `once: true` and `amount: 0.3`. Every choreography finishes within 600 ms of its trigger.
**Critic:** `amount: "some"` instead of 0.3, because a group taller than 3.3 viewports (the evidence strip on a phone) never
reaches 30 % and would stay hidden. Hiding follows principle 7: only after the observer reports the group off screen,
revealed on focus and before print, never under reduced motion.

| Element | Trigger | What moves | Why | Duration / easing | Reduced | Safety note |
|---|---|---|---|---|---|---|
| Verdict banner | report loaded | opacity 0→1, y 8→0. Then the seal (SVG circle plus ✓/✗ path) draws. The non-determinism notice joins the same group | the verdict is the headline, revealed once | emphasis 420, ease-out. Seal 300, ease-draw, +120 ms | instant | banner background vs paper: ΔL 0.08 and darker side 0.82, so not a transition. Text appears once. **Critic:** the notice is never delayed past the banner and never animated on its own |
| Key figures row (new, under the reason) | with banner | **Critic:** no tickers. The figures (violations, most general flashes in one whole second, most red flashes in one whole second, frames captured, culprits) belong to the banner group and arrive with it. Counting up from 0 showed values that were never measured (principle 5), delayed reading, and 600 ms plus a 160 ms stagger overran the 600 ms budget | the measured results arrive with the verdict | with the banner | instant | real report fields only, labelled exactly |
| Timeline bars | chart in view | `scaleY` 0→1 from the baseline (`transform-box: fill-box`, origin bottom), staggered by `staggerDelay(i, n)` | the counts rise from zero to their value | 360, ease-draw. Stagger total 180 or less | instant | one-shot. Bars cover a small share of the plot |
| WCAG 3/s limit line | after bars (+240 ms) | revealed left→right by a `<clipPath>` rect scaled on x, so the dashes never stretch | the threshold is drawn across the measurements | 300, ease-out | instant | 2 px line |
| Violation spans and labels | after line (+380 ms) | opacity 0→1 | the finding arrives after the evidence that causes it | quick 160, ease-out (**Critic:** was "quick 200", which is not a token) | instant | `--fail-bg` vs paper: ΔL 0.08, not a transition |
| Hover readout (new) | pointer over a second | static column tint plus a one-line readout under the chart (`aria-hidden`; the data table stays the accessible path) | answers "how many in this second?" | none (instant) | same | tint is `--paper-deep` at 50 % |
| Data table disclosure | toggle | nothing (unchanged: instant, 350 ms gate) | | | | rejected: an animated height would slide the evidence frames. **Critic:** the disclosure shifts the heatmap and evidence that the overlay toggle also changes, so both share the report's large-area gate (`useLargeAreaGate`); two separate 350 ms gates let alternating clicks change the same cells about 6 times a second |
| Evidence cards | strip in view | **Critic:** opacity 0→1 only, staggered by `staggerDelay(i, n)` (240 ms or less in total). No blur and no y: blur is not a transform or opacity and alters evidence pixels (principle 5), and 6 cards × 60 ms + 360 ms overran 600 ms | the frames arrive as a set | base 240, ease-out | instant | one-shot. Progressive reveal (principle 7) |
| Evidence card hover and press | pointer | border to ink, `--shadow-2` (on a pseudo-element, fading by opacity), static spotlight ring in ink (section 4), press 0.98 | affordance: this opens | quick 160 | colour only | no image zoom: evidence pixels do not move. **Critic:** the press uses `:active:not(:focus-visible)` |
| Frame dialog open | thumbnail click | **Critic:** ghost FLIP instead of an image FLIP. A 1 px ink rectangle with no fill grows from the thumbnail rect to the dialog image rect (translate plus scale; Motion corrects the stroke for scale), and the image fades in at its final place as the ghost lands. Panel fades in. Paper veil fades in. Keyboard-opened (`event.detail === 0`): no ghost, fades only | the frame is the one you clicked, enlarged | ghost base 240, ease-out. Image quick 160 from +120. Panel quick 160. Veil base 240 | instant | open gate 1000 ms, leading plus trailing (a click inside the gate opens when it ends instead of being lost). Veil keeps background ΔL about 0 (section 6). **Critic:** a scaling high-contrast frame (the black and white box of a strobe capture) sweeps its edges across thousands of cells on the same frames, which can make a flash event by itself; the ghost has no fill, so it cannot |
| Frame dialog close | Close, Escape (`cancel` is intercepted) | the panel fades with scale 0.98; when the dialog still shows the opening frame, the ghost shrinks back to the thumbnail (**Critic:** the image itself never zooms back). Then `close()`, then focus returns to the thumbnail | where it went back to | quick 160, ease-out | instant | close is never gated. **Critic:** Chrome closes a dialog on a repeated Escape even when `cancel` is cancelled, so a `close` event that arrives mid-exit finishes the exit at once and still restores focus. The parent keeps the dialog mounted until the exit ends |
| Frame stepping | Previous / Next / arrows | nothing: the image swaps instantly (existing 350 ms limiter; the first step comes 350 ms or more after open) | | | | a crossfade would show frames that never existed. **Critic:** the next frame is decoded first (a preloaded `Image` and `decode()`, neighbours ±1 preloaded) and `src` swaps only then, so a slow load never shows the blank image box between two frames; a blank would add a transition to every step |
| Heatmap overlay | toggle | canvas stays mounted. Opacity 1↔0. **Critic:** the stage carries `data-overlay` (`on` or `off`) so tests can count changes; the existing test counts canvas insertions, which a mounted canvas never makes | the overlay is a layer over the still | quick 160, ease-out (**Critic:** was "quick 200, ease-in-out": not a token, and a steep in-out concentrates a large luminance change) | instant | large area. Shares the report's large-area gate with the data table (section 6) |
| Culprit outline | select rank | the outline glides from the old box to the new one (`layout` plus `layoutId`). The stroke is drawn as `box-shadow`, which Motion corrects for scale, so it stays 3 px. The label uses `layout="position"` | the same highlight moved to a different element | `SPRING.glide`. **Critic:** instant when keyboard-activated | instant | 3 px stroke plus a 2 px white halo, tiny area. Gate 350 ms. **Critic:** one outline element with a constant, namespaced `layoutId`; it is never re-keyed per culprit, and an exiting outline drops `data-testid` so tests and the live caption never read two |
| Culprit outline enter and exit | first select / deselect | opacity plus scale 0.98→1. Exit opacity | appeared or removed in place | quick 160 / 120 | instant | |
| Selected culprit row | select | background to `--pass-bg` plus an inset accent bar | which row is outlined | quick 160 | instant | ΔL 0.07 vs paper |
| Copy CSS | press | copy icon crossfades to check (opacity, scale 0.8→1; **Critic:** no blur, which is neither transform nor opacity). Label becomes "Copied" in a fixed width. Reverts once after 2 s | the clipboard took it | quick 160 | instant | one revert, not periodic. Small area. **Critic:** a click while "Copied" shows copies again and restarts the 2 s timer without replaying the morph; "Copied" is announced in a polite live region |
| Verify, running | press | label "Verifying…" plus status text. Nothing loops | | | | no spinner |
| Verify, result | reloaded report arrives | **Critic:** the After values fade in (opacity, y 4→0) beside the unchanged Before values; ticking from Before to After showed values that never existed (principle 5). Changed cells get a persistent `--pass-bg` tint | shows the fix caused the drop | base 240, ease-out | instant | tint not a transition (darker side above 0.80) |
| Verified badge | `verified` becomes true | "○ Not verified" crossfades to "✓ Verified". The check path draws. The badge settles from scale 0.96 | the state changed because of the verification | base 240, ease-out | instant | badge is about 110 x 24 px |

### 3.4 History (`/history/`)

| Element | Trigger | What moves | Why | Duration / easing | Reduced | Safety note |
|---|---|---|---|---|---|---|
| Rows and cards, first load | list loaded | opacity 0→1, y 4→0, `staggerDelay` (8 rows or fewer staggered; later rows share the last delay) | the list arrives in order, newest first | base 240, ease-out | instant | first load only. A reload after a delete never replays it |
| Deleted row | delete succeeded and list reloaded | `AnimatePresence` exit: opacity 1→0. The row is `inert` and `aria-hidden` while it leaves | the item you deleted goes away | quick 160 | instant | delete gate 1000 ms on top of `confirm()`. **Critic:** focus moves to the next row's Delete (existing rule) in the same commit, before the row becomes `inert`, otherwise it drops to `<body>`; the leaving row loses `data-delete`, so `focusDeleteButton` can never pick it |
| Remaining rows | after the exit | `layout="position"` slides them up to close the gap. Cards use `mode="popLayout"` | the list closed up; nothing else changed | `SPRING.glide` | instant | text slides 49 px or less. Fallback is an instant close if the self-audit flags it. **Critic:** table rows use the default `AnimatePresence` mode, because `popLayout` sets `position: absolute`, which breaks a `<tr>`. A 49 px slide moves dense text coherently, so F4 runs with a viewport full of rows |
| Empty state after the last delete | list empty | opacity, plus a static illustration (section 4) | the list is now empty | base 240 | instant | heading takes focus (existing). **Critic:** the copy says why it is empty and gives the one action ("Run an audit"), per antislop-ui; the drawing is optional |

### 3.5 Rejected candidates (find-animation-opportunities gate)

- **Data table height animation.** Rejected on function: it would slide every evidence frame below it.
- **Frame-step crossfade.** Rejected on function: evidence must be exact.
- **Focus ring transitions and keyboard radio glides.** Rejected on frequency: keyboard actions are always instant.
- **Per-character heading reveals** (Magic UI text-animate, React Bits BlurText). Rejected on purpose: decorative, and they move text.
- **Route exit animations.** Rejected because they need App Router internals.
- **Evidence image hover zoom.** Rejected on function: evidence pixels should not move.
- **Current-step pulse and loading shimmer.** Forbidden: periodic.
- **Critic: count-up tickers** on the report's key figures and on Verify. Rejected on honesty: they show values that were never measured.
- **Critic: image FLIP into the frame viewer, and BlurFade blur.** Rejected on function and safety: evidence pixels must not move, scale or blur, and a moving high-contrast frame can flash by itself.
- **Critic: height tweens** (validation lines, custom row). Rejected on performance: height is a layout property.
- **Critic: scroll-linked animation** (parallax, `useScroll`, scroll progress bars). Rejected on safety: scrolling replays it at any speed, so it cannot be gated.
- **Critic: view transitions** (React `<ViewTransition>`, `document.startViewTransition`, CSS `@view-transition`). Rejected: they run outside the fake clock, and hit-testing goes to the pseudo-element tree.

### 3.6 Missed opportunities (Critic: added, no flash risk)

- **`scrollbar-gutter: stable` on `html`.** Moving between a short page and a long one otherwise shifts every centred element sideways by the scrollbar width: a whole-page jump on each navigation, and one more large-area change in a navigation mash.
- **Decode before swap** for the frame viewer, the thumbnails and the heatmap still, with `width` and `height` set, so nothing reflows or blanks while an image loads.
- **Delayed loading copy** (250 ms, reserved height): no Loading flicker on fast local answers.
- **Hit areas.** `.btn-small` (32 px) gets a 40 px hit area through a non-overlapping `::after`.
- **In-app motion switch** (WCAG 2.3.3 without relying on the OS setting). "Motion: full / reduced" in the footer, kept in `localStorage` (every access in try/catch), sets `data-motion="reduce"` on `<html>`. MotionRoot then uses `reducedMotion="always"` and `skipAnimations`, and the CSS treats `:root[data-motion="reduce"]` exactly like the media query. It also lets the self-audit capture the reduced path (section 7).
- **Print.** `@media print` shows final states (opacity 1, no transforms) and no veil, so a printed report is complete.
- **One tone treatment.** `.notice` and `.state-error` still use 8 px left stripes, the same antislop tell as the old verdict stripe: give them the banner's 1 px tone border plus 4 px top rule, keeping their word and glyph.
- **`::selection`** in `--pass-bg` with ink text, so selected figures stay readable on paper.
- **Chart readout from the keyboard (optional).** Arrow keys on a focusable chart move the static column tint and the readout; the data table stays the full accessible path.

## 4. Look-and-feel upgrades (ink on paper, premium)

- **Type scale** (major third, 16 px base). Sizes in px: 13 (kicker, table headers, meta caps, +0.08em tracking), 14 (hint, caption), 16 (body), 20 (h3), 25 (h2), 39 (h1; 31 on phones), 48 (verdict word; 38 on phones). Line heights: 1.1 for 31 px and up, 1.2 for h2/h3, 1.5 for body. Letter spacing -0.01em at 31 px and up. Use `text-wrap: balance` on h1 to h3 and `pretty` on lede, hint and figcaption. Set `-webkit-font-smoothing: antialiased`. Fonts stay system serif and system sans with tabular numerals.
- **Spacing rhythm.** Base unit 4 px: `--s1`…`--s8` = 4, 8, 12, 16, 24, 32, 48, 64. Section gap 48 (32 on phones). Field block 24, including the message slot. Card padding 20 (16 on phones). Inline `style={{ marginTop: 28 }}` and similar become tokens.
- **Surfaces.** Cards (fix, state, history card, evidence thumb, radio card): `--surface`, `1px solid var(--rule)`, radius 6 px (controls 4 px). `--shadow-1: 0 1px 0 rgb(23 22 19 / .04), 0 1px 2px rgb(23 22 19 / .06)`. On hover and for the dialog, `--shadow-2: 0 1px 2px rgb(23 22 19 / .06), 0 12px 28px -14px rgb(23 22 19 / .22)`. No glass, no glow. The 2 px ink masthead and footer rules stay as the identity.
- **Verdict banner.** Replace the 12 px left stripe (an antislop tell) with a 1 px border in the verdict colour (`color-mix` 45 %) plus a 4 px top rule. Then a 48 px SVG seal and the 48 px serif verdict word side by side, the reason (18 px), a hairline, the key figures row (13 px caps labels over 25 px tabular figures), and the meaning (15 px, muted).
- **Chart.** Solid 1 px grid in `--chart-grid`, 1 px ink axes, square bars (no rounding, for honesty), a 2 px dashed limit line with a paper halo label, 12 px legend swatches, span labels as paper-haloed text. Add the pointer readout from 3.3. Number labels use tabular numerals.
- **Hover and press.** `.btn`: background and border colour (quick). `:active` uses `scale(var(--press-scale))` with `transition: transform var(--dur-press) var(--ease-out)`. `.btn-primary:hover` darkens. Rows: `--paper-deep` tint at 40 %. Never `transition: all`. **Critic:** hover runs at pointer speed and cannot be gated, so no hover pair may be a transition (ΔL 0.10 or more with the darker side below 0.80), in either scheme. Today `.btn:hover` is one (`--paper-deep` L 0.793 on `--surface` L 0.983), and in a narrow desktop window a wide button exceeds 25 % of the scaled window: use `color-mix(in srgb, var(--paper-deep) 60%, var(--surface))` (L 0.864). Dark `--accent-hover` #a2d9b8 against `--accent` #84c9a0 is ΔL 0.116: use #93d1ac (ΔL 0.056, and the dark label gains contrast). The press is `:active:not(:focus-visible)`, so keyboard Space never scales. Hover shadows fade by the opacity of a pseudo-element instead of animating `box-shadow`.
- **Spotlight border.** **Removed (section 8):** a cursor-following ring repeated the hover state (border, shadow) and the selection ring, said nothing about cause or state, and is a recognisable template pattern. Hover is the border colour plus the opacity-faded `--shadow-2`.
- **Images.** Evidence, dialog and heatmap stills get `outline: 1px solid rgb(0 0 0 / .1); outline-offset: -1px` (dark: `rgb(255 255 255 / .1)`).
- **Empty states.** `EmptyState` gets an `illustration` prop: a static inline SVG, 72 x 56, 1.5 px `currentColor` strokes in `--ink-soft`, one accent stroke, `aria-hidden`, no motion. Three drawings: a ledger sheet with a flat trace line (History), a blank frame with dashed corners (Report: no report or not finished), a flat trace on a still frame (Run: no audit). **Critic:** the copy carries the state (why it is empty and the one action, per antislop-ui); a drawing without that copy is the placeholder-empty-state tell, so keep it small or drop it.
- **Dark mode parity.** Every new token is redefined in the dark block. Shadows become `0 0 0 1px var(--rule), inset 0 1px 0 rgb(255 255 255 / .04)`. The veil uses dark `--paper`. The spotlight mix is 35 %. The culprit and window box colours stay fixed because they sit on captured frames. Axe runs both schemes (existing). **Critic:** dark rules live only as tokens in globals.css (CSS modules use tokens and carry no colour-scheme media queries of their own), and globals.css honours `:root[data-theme="dark"]` as well as the media query, so the self-audit can switch schemes in its pre-roll (section 7).

## 5. Component sourcing

| New piece (web/components/motion/) | Source | Kept | Stripped or changed |
|---|---|---|---|
| `NumberTicker` | Magic UI number-ticker (MCP); React Bits CountUp (registry) as reference for from→to | motion value written to `textContent`, tabular numerals, `useInView({ once: true })` | `useSpring` (damping 60, stiffness 100, settles well past 600 ms) replaced by a bounded tween `animate(mv, to, { duration ≤ 0.6 })`. Also removed: Tailwind and `cn`, CountUp's roll mode (moving digit columns), `replayOnHover` (repeats) and overshoot easing. Added: `aria-hidden` digits plus visually hidden final value, follow mode, instant under reduced motion. **Critic:** used only for the Running frame counter (follow mode); never counts from 0 |
| `Reveal` (from BlurFade) | Magic UI blur-fade (MCP) | opacity and y variants with delay | **Critic:** blur removed entirely (neither transform nor opacity, and it alters evidence). Offset 4 px or less, none on evidence. Hides only after the observer reports off screen; reveals on intersection (`amount: "some"`), `focusin` and `beforeprint`; `initial={false}` under reduce. Removed: `AnimatePresence` exit replays, the default `inView=false`, Tailwind. Used only for data-driven content |
| `AnimatedRows` | Magic UI animated-list (MCP) | `AnimatePresence` plus `layout` for exits and gap closing | removed: the `setTimeout` reveal loop (one item per second, periodic), `scale: 0` entrances (never scale from 0) and the bouncy spring. Stagger on first load only |
| `useSpotlight` plus `.spotlight` CSS | React Bits SpotlightCard (registry, TS-CSS) | n/a | **Removed (section 8):** decoration that duplicated the hover state |
| `FrameGhost` (FLIP in FrameDialog; **Critic:** was `FrameZoom`) | Aceternity expandable-cards (MCP) for the grow-from-card idea; Motion `animate()` | thumbnail rect to dialog image rect via transform, on an unfilled 1 px ink rectangle; the image never moves | `layoutId` replaced by an explicit FLIP, because a native `<dialog>` enters the top layer in `showModal()` after Motion measures. Removed: near-black overlay, outside-click hook, Tailwind |
| Outline glide, nav underline, radio indicator | Motion `layout` / `layoutId` (motion skill) | shared-element glide | spring bounce 0. Stroke as `box-shadow` (scale-corrected). **Critic:** `layoutId`s namespaced by route, one `LayoutGroup` per page |
| Check, seal and limit-line draws | Motion `pathLength`; SVG `clipPath` | one-shot draw | no looping dash offsets |
| Rejected: Aceternity layout-grid, Magic UI text-animate / shine-border / border-beam / meteors / particles, React Bits backgrounds | MCP / registry | n/a | looping, decorative, or a black 0.8 backdrop |

## 6. Safety analysis

**How a flash is counted** (engine, docs/SPEC.md 5.6 and 5.7).
- A transition is a change of 10 % or more in relative luminance, where the darker state is below 0.80.
- A flash is a pair of opposing transitions, measured on 4 x 4 px cells.
- A frame is a flash event only when the cells completing a flash in it (or in the frame before: the 1-frame tolerance) cover more than 25 % of some window. The window is 341 x 256 px at 1024 wide. **Critic:** at 1280 the engine's rounding gives 107 x 80 cells, 428 x 320 px, so 25 % is 34,240 px² (was 426 x 320 and 34,100). The window scales with viewport width: at 600 px it is 200 x 152 px (25 % = 7,600 px²) and at 390 px it is 128 x 96 px (25 % = 3,072 px²), so an element that is small at 1280 can be large on a phone or in a narrow window.
- A capture fails when more than 3 events fall in any sliding second.

**What follows for motion.**
- A one-shot change gives each cell at most one transition. It cannot complete a pair, so it cannot make an event on its own.
- Movement of small or sparse shapes (glides, outlines, underlines) completes flashes in different cells on different frames, so it rarely reaches 25 % in one frame. **Critic:** this does not hold for coherent movement of dense text or high-contrast imagery, where every glyph or edge crosses its cells on the same frames. Hence no whole-page slide, no moving evidence image, and list slides tested with a viewport full of rows.
- **Critic:** hover and pointer reactions cannot be gated, so their colour pairs must not be transitions at all (ΔL below 0.10, or the darker side at 0.80 or above), checked in both schemes by a token test. Hover never moves or scales a surface larger than 5,000 px².
- Synchronised area changes that can be reversed (fades, toggles, backdrops, content swaps) are the real risk. Each one has a reversal source, and each source is gated. **Critic:** sources that change the same cells share one gate (the report's overlay and data table), because two independent 350 ms gates allow about 6 changes a second between them.

Luminance values below are computed from the tokens. Light mode is the worst case: dark `--paper` has L 0.007, so dark backgrounds never transition. **Critic:** dark text cells do transition (ink L 0.809 on paper 0.007), about as much as light ones, and the viewer shows the same captured frames in both schemes, so the counts below hold for both; only the hover pairs differ (above).

| Large-area change | Area (1280 x 900) | Worst ΔL | Reversal source and gate | Worst events in any 1 s |
|---|---|---|---|---|
| Route entrance | main, up to about 1070 x 740 | background paper→paper 0. Text cells about 0.89→0.3 | navigation, leading plus trailing gate 350 ms. Animated entrance (opacity only) 1000 ms or more apart (adds one blank step) | 3 swaps + 1 blank step = 4 transitions per cell, so 2 or fewer |
| **Critic:** history traversal (Back, Forward, a held Alt+Left, back-forward cache) | main | as above | route display gate: a burst shows old, blank, final | at most 2 transitions per burst and 4 per second, so 2 or fewer (without the gate: up to about 15) |
| Frame dialog open and close | panel about 1100 x 790 | veil about 0 on background (paper veil, not a dim; the old 50 % dim went 0.898→0.227). Surface vs paper 0.085 (below 0.1). Image black box 0.89→0.01 | open gate 1000 ms. Close always allowed. Steps share the 350 ms limiter. **Critic:** the image only fades (ghost FLIP) and frames swap after decode | open fade, close fade and up to three steps in a sliding second = 5 transitions, so 2 or fewer. **Critic:** the old image sweep could add a flash event of its own on every open and close, which put the worst second at 3 events, on the limit |
| Heatmap overlay crossfade | up to 880 x 660 | about 0.8. **Critic:** red: the ramp's reddest stop (128, 0, 38) has a redness ratio of 0.77, below 0.8, so the overlay makes no red transitions | shared large-area gate, 350 ms. Fade 160 ms finishes before the next change | 3 changes, so 2 or fewer |
| Custom-size row and shift below | about 620 x 80 inserted; content below shifts 80 px | text cells about 0.6 | leading plus trailing `useThrottledValue` 350 ms (**Critic**) | 2 or fewer |
| Data table disclosure plus heatmap overlay (**Critic:** analysed together) | 300 to 800 px shift; overlay up to 880 x 660 | text cells; overlay about 0.8 | one shared 350 ms gate for both controls | 3 changes per second across both, so 2 or fewer (with separate gates: about 6 changes, so 3, on the limit) |
| History delete: fade plus gap close | row 1070 x 49, rows below slide 49 px | text cells about 0.6 | delete gate 1000 ms plus `confirm()` | 1 or fewer |
| Banner, bars, evidence, badge, spans | various | one-shot | none (report reloads never replay) | 0 |
| Number tickers (**Critic:** the Running frame counter only) | about 1,500 px², one per screen; below 3,072 px² even at 390 px wide | digits change up to about 24 times in 400 ms | area below threshold at every width | 0, because it cannot reach 25 % |
| Glides, indicator, underline, spotlight ring | strokes 3 px or less, rings 1 px | indicator tint 0.983→0.831 (darker side above 0.80) | | 0 |
| **Critic:** hover and focus colours | any control or row | `.btn` 0.983→0.864, rows 0.898→0.856, dark primary 0.492→0.548: none is a transition | none possible (pointer speed) | 0 at any width |

**Forbidden patterns.** Enforced in review, in Playwright, and by the self-audit:
- `repeat: Infinity` or any `repeat` above 0, `animation-iteration-count` other than 1, CSS `infinite`.
- Spinners, shimmer, pulse, marquee, meteors, beams, sparkles, particles, animated gradients or noise.
- Indeterminate `<progress>`.
- Any one-shot animation over 600 ms, or a `setInterval` or rAF loop without a stop.
- `scale(0)` entrances, near-black backdrops, a frame-step crossfade, `transition: all`.
- **Critic:** any animated `filter` (blur included); height or width tweens; count-up from 0; scroll-linked animation (`useScroll`-driven transforms, `animation-timeline`, parallax); view transitions (`document.startViewTransition`, `<ViewTransition>`, `@view-transition`); CSS `scroll-behavior: smooth`; an un-namespaced `layoutId`; Motion `onTap` or `whileTap` as the trigger of an action (actions use `click`, which keyboards and the self-audit's `.click()` reach); hover that moves or scales a surface larger than 5,000 px²; a hover colour pair that is a transition.
- Ungated controls that flip a large area. Emoji. Purple gradients. Glassmorphism. Neon.

**Playwright changes.**
- behaviour.spec.ts "SAFETY" currently requires non-transition animations to last 1000 ms or more. That conflicts with ADR-024 and must be replaced by: every `Animation` has `iterations === 1`, finite duration of 600 ms or less, no `Infinity`; every `CSSTransition` lasts 200 ms or less.
- The reduced-motion test stays as it is (`getAnimations().length === 0`). `MotionGlobalConfig.skipAnimations` is what makes it hold.
- Before every axe run, wait for `Promise.all(document.getAnimations().map(a => a.finished))`, so contrast is measured on settled frames. **Critic:** that only sees CSS and WAAPI animations; Motion's layout animations, springs, `x`/`y` and motion values run on requestAnimationFrame. So also wait 700 ms after the last trigger (every choreography ends within 600 ms), scroll through the page in viewport steps first so in-view groups are revealed (axe skips opacity-0 content, which would leave its contrast unmeasured), and run axe once more under reduced motion.
- **Critic:** rAF watchdog. An init script counts requestAnimationFrame callbacks. 700 ms after the last input, the next 500 ms must have none, on every screen and after every scripted interaction, with and without reduced motion. It is the only check that catches a looping JavaScript animation.
- **Critic:** settle check. Screenshots 700 ms and 1,400 ms after the last input are pixel-identical. Under reduced motion, the screenshot taken as soon as the screen is ready already equals the one 700 ms later, and no element in `main` has a computed opacity below 1 (nothing waits for an in-view trigger). The same opacity check runs under `emulateMedia({ media: "print" })`.
- **Critic:** the heatmap toggle test counts `data-overlay` changes; with the canvas kept mounted its childList count is always 0, so the test would pass without testing anything. The culprit test relies on the exiting outline dropping `data-testid`. The progress test keeps reading the native `progress`.
- **Critic:** new tests. Alternating clicks on "Heatmap overlay" and the data table toggle every 50 ms change the screen at most 1 + elapsed/350 times in total. Space on a keyboard-focused button starts no transform animation. A token test parses globals.css and checks every hover pair against the transition rule in both schemes. A static scan of `web/app`, `web/components` and `web/lib` fails on the forbidden patterns above (`repeat:`, `Infinity`, `infinite`, `iteration-count`, `setInterval`, `filter` inside a transition or animation, `transition: all`, `startViewTransition`, `ViewTransition`, `useScroll`, a `layoutId` without a `:` namespace).

## 7. Self-audit design (ADR-024 proof: FlashTrace audits itself)

**`tests/ui_mock_server.py`** (stdlib only).
- Interface: `serve(out_dir, fixtures_dir, port=0) -> (ThreadingHTTPServer, base_url)` on a daemon thread. `python tests/ui_mock_server.py --port 4318` runs it by hand.
- Static files come from `web/out`. A directory serves `index.html`. The `?_rsc` query is ignored, so `.txt` payloads load on client navigation. Unknown paths serve `404.html`. Responses carry `Cache-Control: no-store`.
- It answers the docs/API.md endpoints from `web/tests/fixtures`, with the same IDs and semantics as `web/tests/mock-api.ts`:
  - Frames: FAIL serves `frame-a`/`frame-b` chosen by `floor(i*8/60) % 2`. Every other job serves `frame-still`. **Critic:** `serve(..., frames="alternate")` switches to `i % 2` for F1, so every frame step is a full black and white swap of the 420 x 300 box; with the 4 Hz square wave most steps change nothing and the limiter goes untested.
  - `GET /jobs/<RUNNING>` advances by request count (312→432→552→600, then analyzing, attributing, done), so the hand-off is deterministic. When done, it serves report-fail.json.
  - `POST verify` puts the job in phase verifying. The next GET answers done and sets `fixes[n].after` and `verified`.
  - `DELETE` removes the job from the list.
  - **Critic:** `serve(..., jobs=N)` sets the number of finished jobs: 24 for F4, so the rows that slide up fill the viewport (the worst case for coherent text movement), and 1 for F4b.
- State lives in each server instance, and each scenario gets a fresh server.
- A test-only `/__control/strobe.html` is headless-only and never opened on screen: a full-viewport black and white swap at 5 Hz, driven by `setTimeout`. **Critic:** "never opened on screen" must be enforced, because the server can be run by hand: the page swaps only when `navigator.webdriver === true` and the URL has `?run=1` (C0 uses both), otherwise it shows static warning text. It is never linked from the UI, in the spirit of SPEC 9.2.

**`tests/test_ui_self_audit.py`**
- Marked `pytestmark = pytest.mark.browser`. Register the mark in pyproject `markers`, because capture/tests/conftest.py is not loaded for `tests/`.
- Skips the module with "web/out is missing: run `npm run build` in web/ first (the self-audit audits the built UI)". Also skips when Chromium is unavailable.
- One module-scoped Chromium is passed as `browser=`.
- For each scenario, it calls `capture(base + path, duration_s, viewport=(1280, 900), fps=60, interaction_script=PRELUDE + script, browser=chromium)` and then `engine.analyze(result.frames, fps=60, viewport=(1280, 900))`. It asserts:
  - `result.meta.deterministic`, otherwise the result is only indicative and the test fails with `meta.notes`.
  - The frames are not all identical, so the scenario really ran.
  - `analysis.verdict == "PASS"`, with `analysis.reason` in the failure message.
  - **Critic:** the scenario really ran: `changed_runs` (maximal runs of consecutive frames whose hash differs from the one before) is at least the scenario's `min_runs`. "Not all identical" passes even when a mash script died after its first click.
  - **Critic:** reproducible: each scenario is captured twice and `result.meta.hash` must match. The double-shot check compares only the first and last frames, and IntersectionObserver and network ordering are worth proving.
- **Critic:** passes. Every scenario runs at 1280 x 900 in light mode. E1, E4, F1, F2, F6 and F10 also run at 390 x 844 (the window is 128 x 96 px there, so small elements matter) and in dark mode (the pre-roll sets `data-theme="dark"` on `<html>`; `capture()` itself always emulates light). Once the in-app motion switch exists (3.6), E4 and F2 also run with `data-motion="reduce"`.
- Recording: `record_property` stores `general_max_rate`, `red_max_rate` and the event count, and a terminal summary table is printed. These are measurements in the test log, never pasted into docs (repo rule: figures live only in docs/RESULTS.md).

```js
// PRELUDE. Awaited waits run in the capture's pre-roll and are not recorded. Calls scheduled with `every` fire during
// the recorded frames, because setTimeout runs on the capture's fake clock.
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(find, ms = 4000) { for (let t = 0; t <= ms; t += 50) { const el = find(); if (el) return el; await wait(50); } throw new Error("self-audit: element not found"); }
const byText = (text, root = document) => [...root.querySelectorAll("button, a")].find((el) => el.textContent.trim().startsWith(text));
const every = (ms, count, act) => { for (let i = 0; i < count; i++) setTimeout(() => act(i), i * ms); };
// Critic: resolve elements in the awaited pre-roll and keep the handle. Labels change ("Copy CSS" → "Copied",
// "Show data table" → "Hide data table"), so byText() inside a timer returns undefined and the mash silently stops.
const byLabel = (text) => [...document.querySelectorAll("label")].find((l) => l.textContent.trim().startsWith(text))?.control;
const jump = (y) => window.scrollTo({ top: y, behavior: "instant" }); // never smooth: smooth scrolling runs outside the fake clock
```

| Scenario | Path | Duration (s) | Script (recorded part) | Proves |
|---|---|---|---|---|
| E1 new-audit-entrance | `/` | 3 | none | form arrival, nothing hidden server-side |
| E2 route-to-history | `/` | 3 | at 300 ms, click the "History" nav link | route entrance, nav underline glide, row stagger |
| E3 running-handoff | `/run/?id=RUNNING` | 8 (**Critic:** was 4; the job needs 6 polls to reach done, then the 450 ms hold and the report entrance) | none (polls every 1 s of virtual time) | fill tween, ticker, check draws, hold, report entrance |
| E4 report-entrance | `/report/?id=FAIL` | 3 | none | banner group with the key figures (**Critic:** no tickers), seal |
| E5 report-scroll | `/report/?id=FAIL` | 4 | `jump()` to the next section every 700 ms, 5 times | in-view bars, limit line, spans, evidence fade (**Critic:** no blur) |
| F1 dialog-mash | `/report/?id=FAIL`, `frames="alternate"` | 4 | every 120 ms, 30 times: click Close if open, else click thumbnail 2. Every 33 ms: click "Next frame" | open gate, veil, ghost FLIP, step limiter on full black and white swaps |
| F2 heatmap-autorepeat | `/report/?id=FAIL` | 3 | click "Heatmap overlay" every 33 ms, 90 times (keyboard auto-repeat speed) | 350 ms gate plus crossfade |
| F3 culprit-mash | `/report/?id=FAIL` | 3 | click the Outline buttons of ranks 1, 2, 3 in turn every 50 ms, 60 times | outline glide plus gate |
| F4 history-delete | `/history/`, `jobs=24` | 6 | `window.confirm = () => true`, then click the first Delete every 100 ms, 60 times | delete gate, exit, gap close over a viewport full of rows (**Critic:** with 5 jobs and a 1000 ms gate, 4 s never reached the empty state it claimed) |
| F4b last-delete (**Critic:** new) | `/history/`, `jobs=1` | 3 | `window.confirm = () => true`, then at 300 ms click Delete | exit, empty state, heading focus |
| F5 preset-churn | `/` | 3 | click the viewport radios in order every 40 ms, 75 times (Custom at 5 Hz) | indicator glide, trailing gate on the custom row |
| F6 nav-mash | `/` | 3 | click History and New audit alternately every 100 ms, 30 times | navigation gate plus 1000 ms route-animation gap |
| F7 verify | `/report/?id=FAIL` | 4 | click "Verify this fix" on fix 1 | After values fading in (**Critic:** no tickers), cell tint, badge settle |
| F8 copy-mash | `/report/?id=FAIL` | 3 | click the "Copy CSS" button handle (resolved in the pre-roll) every 50 ms, 60 times. **Critic:** grant `clipboard-write` or stub `navigator.clipboard.writeText` in the pre-roll, or "Copied" never shows | icon morph, no replay while "Copied" |
| F9 shared-gate mash (**Critic:** new) | `/report/?id=FAIL` | 3 | every 60 ms, 45 times: click "Heatmap overlay" and the data table toggle alternately (handles from the pre-roll) | the shared large-area gate |
| F10 history traversal (**Critic:** new) | `/` | 3 | pre-roll: client-navigate to History, then to New audit. Recorded: `history.back()` and `history.forward()` alternately every 33 ms, 60 times | route display gate |
| C0 control (must FAIL) | `/__control/strobe.html` | 2 | none | the pipeline sees flashing at this viewport and fps, so a PASS is not vacuous |

**Not covered, by design.** Dark mode and reduced motion, because `capture()` has no colour-scheme or media emulation option (capture/ is owned elsewhere). Proposed follow-up: a `color_scheme` argument. Under reduced motion nothing animates, which `behaviour.spec.ts` already asserts.
**Critic:** dark mode and the reduced path are now covered through `data-theme` and `data-motion` set in the pre-roll
(above), without touching capture/; the `color_scheme` argument stays the cleaner follow-up. Still not covered: CSS
`:hover` and `:active` (script cannot trigger them; the token test and the 5,000 px² hover rule cover them), real
network latency (capture answers every request instantly, so decode-before-swap is tested in Playwright with a delayed
frame route), and the OS media query itself (`behaviour.spec.ts`).

## 8. Self-audit amendments (2026-10-25)

The first self-audit runs flagged the items below. Each was fixed in the UI, not in the test, as section 7 requires.
Where an amendment contradicts a row above, the amendment wins. The measured rates are in the test log only, not here.

**Safety (the engine's verdict or a rate at the limit):**
- **Custom-size row (3.1).** The blocks below now move at once instead of gliding. In F5 (radio churn), the 80 px glide moved dense text so that every glyph crossed its cells on the same frames. Each throttled toggle then completed several flash events: FAIL. One jump per toggle changes each cell once.
- **Frame viewer (3.3).** Closing is instant: no exit fade and no shrink-back ghost. A fade that reverses completes the viewer's flashes on several frames, because each contrast group crosses the threshold at a different point of the fade. The flashes are the veil over a black evidence thumbnail, the panel text and the captured frame. An instant close completes them on one frame. The open gate also restarts when a close finishes, so the viewer stays shut for 1 s between opens. F1 at 390 px measured two open-and-close cycles inside one second: FAIL. The veil fades in over 160 ms with the panel, and the picture rides the panel's fade (no separate delayed fade). The panel never scales (principle 3: no large-area movement).
- **History delete (3.4).** The 1 s cool-down also restarts when the leaving row's exit has finished (`AnimatePresence` `onExitComplete`), as the original comment intended ("after the previous row actually left"). F4 was at the limit with the cool-down counted from the server's answer.
- **Nav (3.0).** The active link and its underline mark the page that is on screen (`setDisplayedRoute` from `template.tsx`), not the router's latest location. During a Back/Forward burst held by the route display gate they stay put and move once, with the final page. History traversal moves the underline instantly, like the keyboard (no nav item was chosen). A held route reserves a viewport of height, so the footer does not jump between page heights during a burst.

**Determinism (two captures of the same scenario must be identical):**
- **Motion never uses Web Animations here.** Every motion element that fades spreads `PAGE_CLOCK` (an `onUpdate` no-op), so Motion runs opacity on requestAnimationFrame like every other value. Motion starts a Web Animation by setting `animation.startTime` from its own clock. Under the capture's fake page clock and real animation timeline, a fade could finish and commit before the capture paused it. Section 3.0's "Motion runs on page requestAnimationFrame and WAAPI" now reads "on page requestAnimationFrame only". CSS transitions (hover, the heatmap overlay) are unchanged.
- **Nothing is scaled with a layout projection.** The culprit outline is an SVG rect whose x, y, width and height animate, with a non-scaling 3 px stroke and a white halo either side, and the label moves by left/top. The frame ghost is an SVG rect in the same way. Chromium re-rasterises a scaled layer on its own real-time schedule, so the scaled versions differed by a pixel at scattered frames. The outline enters with a plain fade (no 0.98 scale). Translate-only glides (nav underline, radio indicator) keep `layoutId`.
- **Updates from outside input events commit on the page clock.** Network results and IntersectionObserver reports go through `onPageClock` (`lib/usePolling.ts`). It applies them in the next animation frame with `flushSync`, and a 100 ms timer covers hidden tabs. Animation and frame callbacks, the open gate's trailing open, the route hold timer and `useThrottledValue` also commit with `flushSync`, inside the clock step that caused them. Otherwise React commits them in a task of its own, and two captures could see them a frame apart, or an entrance could start at a slightly different time within a frame.
- **Evidence stills** load eagerly with `decoding="sync"` (thumbnails, viewer image, heatmap still), so a card never paints an empty box for a frame while its picture decodes (3.6).
- **Principle 7, in-view groups.** In-view groups start in their final state and are hidden only after the observer reports them off screen. A group already on screen at its first observation simply stays (no replay).

**Test calibration:** scripted clicks dispatch a click with `detail: 1` (`press()` in the PRELUDE). `el.click()` has detail 0, which the UI reads as a keyboard click (instant, no movement), and the worst case is the moving one. For mashes whose changes merge into one long run (F3, F5, F10), a `min_last_change_s` check proves the script kept running, because the run count cannot. Recorded actions are scheduled from frame 0, which is the end of the capture's 16 ms pre-roll step in which the script finishes, plus half a step, so none is due exactly on the boundary (`at()` and `every()` in the PRELUDE). Scheduling from "now" put the mash at different offsets in two captures, because the awaited waits end at any point inside a step. E4 under reduced motion runs as **E4r**: it opens the FAIL report from History during the recording. Loaded data is now applied on the page clock, so a report opened directly is already complete at frame 0, and with nothing animating there would be nothing to record.

**Proposed capture follow-ups (capture/ is owned elsewhere):** apply a page-assigned `animation.startTime` on the fake timeline, or pause a Web Animation at creation. Before each screenshot, let the page's pending tasks drain (for example a `MessageChannel` round trip after the network wait), so a page needs no `flushSync` to be reproducible.

### 8.1 Review amendments (2026-10-25, second pass)

A safety and accessibility review attacked the UI at 390 px and in dark mode, and a feel review checked it against
the user's skills. Safety findings won over feel findings wherever they touched the same code. Each change below is
pinned by a self-audit scenario (section 7, names in brackets) or a Playwright test. Measured rates stay in the test
log.

**Safety:**
- **History delete (3.4).** The gap closes at once, after the exit fade. There is no `layout` glide, the cards use the
  default presence mode (no `popLayout`), and a leaving card keeps its place until its fade ends. The exit sets
  `display: none` in its own `transitionEnd`, so the gap closes on the animation frame where the fade ends, not when
  React later removes the item in a task of its own. That task could land a frame apart in two captures [F4]. At 390 px the glide
  slid the full-width cards up by about 195 px, and one ordinary delete failed [F4c]. The row and card entrance stagger
  is gone too: History is a utility list the user returns to.
- **Route display gate (3.0).** It is measured from the last display change (any mount, or a held page being shown),
  not from the last mount, and `GATE_MS.routeDisplay` is 500 ms. A page that mounts within 500 ms of a display change
  is held. It is shown 500 ms after its own mount, unless a later mount replaces it. Two cases failed before:
  Back, then Forward 50 ms later, every 450 ms [F10b], and History, then the brand link 50 ms later [F6b]. 500 ms,
  not 350: a page shown at once followed by a held mount is two changes that no gate can prevent. The next such pair
  must start at least a second later, so the worst case is 2 events a second.
- **Masthead navigation.** The brand link and both nav links share one leading plus trailing gate in module scope
  (`BrandLink` in `components/NavLinks.tsx`). The route display gate covers every other link.
- **Heatmap caption (3.3).** The visible caption is constant. The culprit sentence moved into a visually hidden polite
  live region, and the overlay phrase no longer toggles. At 390 px each selection re-wrapped the caption and moved
  everything below it, the pressed Outline button included [F3, F3b]. Culprit selection now uses the report's shared
  large-area gate with the overlay and the data table [F3c].
- **Culprit outline glide (3.3).** The glide runs only when the viewport is 1024 px wide or more (`useGlideRoom`).
  Below that, the outline and its label move to the new box at once: one change per selection. The flash window
  scales with the viewport (128 x 96 px at 390), but the 3 px strokes and the 12 px label do not. At 390 px the label,
  a dark block of text, and the strokes swept across the light frame and completed flashes on three frames per glide
  [F3 at 390 px: 6 a second, then 1].
- **Reduced-motion switch (3.6).** `useInViewArrival` turns `waiting` and `play` into `instant` while it is disabled.
  Content that was scrolled into view under reduced motion is never hidden again, and its entrance is not replayed,
  when motion is turned back on [S1]. The switch has a 350 ms gate and ignores a held Enter.
- **Custom-size row (3.1).** The row is always present. Its inputs are disabled, and show the chosen preset's size,
  until Custom size is chosen. They keep the surface background: a darker fill would be a flash transition under
  radio churn. Choosing a size never moves anything below it [F5 at 390 px]. `useThrottledValue` is no longer used
  there.
- **Frame viewer (3.3).** Steps are limited by `GATE_MS.viewerStep` (500 ms). The limiter's wait starts again when the
  opening fade ends, so the first step comes 500 ms or more after the viewer is fully shown. Closing stays instant
  and ungated. F1 now opens, steps as soon as allowed, closes as soon as the second step shows and reopens at once.
  The old F1 always closed before the first allowed step. Bound: open, step, step and close can no longer fall inside
  one second, so 4 events (FAIL) is impossible. With 350 ms, a close 2 frames after the second step made 4. Two
  steps and a close 1 or 2 frames later still give 3 in a second at 390 px (on the limit, never over it). Reaching
  2 would need steps a second apart, which is not worth it for a manual frame viewer.
- **Keyboard-opened viewer.** It appears at once, panel and veil together (`data-instant` on the `<dialog>` turns
  off the veil's keyframes). Focus therefore never lands on "Next frame" while the panel is still transparent. This
  amends principle 3 for the viewer: an instant change completes each cell's transition on one frame, which is
  never worse for the flash count than a fade.
- **Reduced motion and `::backdrop`.** Both reduce blocks in globals.css include `*::backdrop`, so an open viewer
  leaves no finished `veil-in` animation in `document.getAnimations()`.

**Feel:**
- **Ghost (3.3).** The ghost lands on the picture as drawn inside the letterboxed `<img>` (object-fit: contain, inside
  its 1 px border), so the 4:3 thumbnail zooms into the 4:3 frame. It takes `DUR.quick`, so it arrives on the frame the
  panel, veil and picture reach full opacity.
- **Radio cards (3.1).** The selection ring and tint fade in on the chosen card, in place (`DUR.quick`). The old ring
  goes at once, and there is no fade on first render or for an arrow-key change. The glide crossed the cards in
  between (showing the wrong option as selected) and distorted the ring. `GlideIndicator` is now used only for the nav
  underline.
- **Spotlight.** Removed (sections 4 and 5).
- **Timeline (3.3).** The WCAG limit line is drawn from the first frame, like the axes, with no clip-path wipe. The
  bars rise against it (`DUR.base`, ease-out, stagger of 120 ms or less). The violation spans fade in from +240 ms.
  Everything ends by 400 ms.
- **Evidence strip (3.3).** It has no arrival animation. The frames are static evidence, and the ghost into the
  viewer is their one motion. The timeline is the report's only staggered entrance.
- **Running counter (3.2).** The count is set in the system sans, with lining tabular figures. It ticks for
  `DUR.emphasis`, the same as the fill, so both land on the same frame.
- **Durations.** Component durations come from `DUR` only (the seal is `DUR.base`, the ticker `DUR.emphasis`).
- **Destructive buttons.** They are neutral at rest (`--danger-ink`, `--rule-strong` border) and turn red on hover
  (tint plus text) or focus (text). `--danger-ink` is within 0.10 of `--fail` in luminance in each scheme, so the hover
  is not a flash transition. On a History card, Delete sits at the far edge, away from "Open report".
- **Verdict figures.** The labels are in sentence case at 14 px ("Most red flashes in a whole second"), not tracked
  capitals.
- **Dark elevation.** Dark `--shadow-2` adds a `--rule-strong` ring and a deep drop shadow, and dark `--surface` is
  `#1d1b18`. The thumbnail hover lift and the viewer read as raised in both themes.

- **Verify (3.3).** The button reads "Verifying…" until the reloaded report has arrived, and the busy state ends in
  the same page-clock commit as the result. It used to show "Verify this fix" for one frame in between, committed in a
  React task of its own [F7, reproducibility].

**Self-audit:** every F scenario now runs at 1280 light, 390 light and 1280 dark (F2 also with reduced motion). New
scenarios: F3b (one Outline button every 30 ms), F3c (a round-robin of overlay, table, Outline buttons and rows),
F4c (one delete), F6b (History and brand-link pairs), F10b (Back and Forward pairs) and S1 (the motion switch after
scrolling under reduced motion). F6 and F10b run for 4 s, so the final page shown after the hold is recorded.

**Reproducibility under concurrent captures (ADR-025).** The suite was also run as four concurrent pytest processes
on a loaded machine. Every verdict holds under that load. Three UI changes came out of it:
- A network result is applied no sooner than `NETWORK_SETTLE_MS` (50 ms of page clock) after its request started
  (`onPageClock(update, requestStartedAt)` in `lib/usePolling.ts`; polling, loads, Verify and History's delete). The
  capture finishes a request while virtual time stands still, but reading the body is a further task. Under load it
  finished a capture step later, and the result (with the colour transition it started) appeared a frame apart in two
  captures [F4, F4c, F7].
- History's delete and Verify end their busy state in that same page-clock commit, not in a `finally` that React
  commits in a task of its own.
- The History exit's `display: none` lands on the animation frame (see above).

Captures that depend on a route mount or an IntersectionObserver report (E5, F6, F6b, F10b) can still differ by a
frame between two concurrent captures. Chromium delivers those in tasks that the capture does not drain before a
screenshot, so under load they land a frame later, and the UI cannot pin those moments itself. The fix is the
capture follow-up already proposed above: let the page's pending tasks drain before each screenshot.

