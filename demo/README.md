# FlashTrace demo site

`demo/site/` is "Courtside Live", a fictional basketball match-centre page built as a hazardous test subject for FlashTrace. It is static HTML/CSS with a few lines of plain JavaScript (one-shot clock render and the pause button); no framework, no network resources, no AI.

**Safety.** The page flashes at 4-5 Hz on purpose. It is served only by `flashtrace demo` (`demo/serve.py`), bound to 127.0.0.1, and only behind the warning interstitial at `/` (`demo/index.html`). Do not open `/site/` if you are photosensitive; the audit runs headless and needs nobody to look at it. The page honours `prefers-reduced-motion` (all animation removed, notice shown) and has a "Pause all motion" button (`#pause-animations`) that freezes every animation.

| Element | Animation | Expected FlashTrace result |
|---|---|---|
| `#loader` | 420x300 px, white rgb(255,255,255) <-> rgb(20,20,20), 5 Hz, `steps(1,end)` | **Flagged.** General flash: luminance change about 0.99, area far over 25% of a 341x256 px window, 5 flashes/s > 3. |
| `#live-badge` | 96x32 px, red rgb(255,0,0) <-> rgb(40,40,40), 4 Hz | **Passes** the area rule: 3 072 px is about 3.5% of a 341x256 window (threshold is more than 25%, i.e. more than 21 824 px). Fast but small. |
| `#next-up` | opacity 1 -> 0.6 -> 1 over 2 s | **Passes** on rate: 0.5 flashes/s, well under three per second. |

Serve: `flashtrace demo` (once the CLI lands), or `.venv/bin/python demo/serve.py --port 8765`, then `flashtrace audit http://127.0.0.1:8765/site/`.

Analysis aid, not a compliance certification.
