"""HTML templates for the benchmark pages.

Each page is minimal valid HTML5 with no external resources: inline CSS,
inline scripts, system fonts, and (for video pages) a WebM file that sits
next to the page. Every flashing element is `position: fixed` at a
cell-aligned rectangle, and every animation starts at page time 0 and runs
forever, so FlashTrace's fake clock can step through it frame by frame.

The flashing state at time t seconds is `floor(t * 2 * hz) % 2`:
0 means colour A (the darker state), 1 means colour B.
"""
import json

from bench.colors import css_rgb
from bench.params import VIEWPORT_PX

TEXT_COLOUR = (51, 51, 51)
MARQUEE_COLOUR = (34, 34, 34)
# The fade block's colour has relative luminance 0.8287. The darker end of its
# fade therefore stays at or above 0.80, and SPEC T2 only counts a general
# transition when "the relative luminance of the darker image is below 0.80":
# the fade is an animation the attribution step must ignore, not a flash.
FADE_COLOUR = (224, 236, 252)
FADE_PERIOD_S = 2             # 0.5 Hz
MARQUEE_SPEED_PX_S = 120
MARQUEE_TRAVEL_PX = 1200      # from +400 px to -800 px, 10 s at 120 px/s

PARAGRAPH = ("The highlighted blocks change colour on a fixed schedule so that FlashTrace can be "
             "checked against a known answer. Everything else is static or moves slowly and must "
             "not be reported.")
VIDEO_UNAVAILABLE = ("Video unavailable: ffmpeg was not found when this page was generated, so the "
                     "blocks are static and this page is skipped in the benchmark score.")


def rect_style(rect):
    """Inline CSS that pins an element to a pixel rectangle."""
    return (f"left:{rect['x']}px;top:{rect['y']}px;"
            f"width:{rect['w']}px;height:{rect['h']}px")


def period_css(rate_hz):
    """One full on/off cycle as a CSS duration, e.g. '333.3333ms' for 3 Hz."""
    return f"{1000 / rate_hz:.4f}ms"


def styles(background, mechanism, colour_a, colour_b, rate_hz):
    """The page stylesheet. Only the css mechanism animates from here."""
    width, height = VIEWPORT_PX
    rules = [
        f"html, body {{ margin: 0; width: {width}px; height: {height}px; overflow: hidden; "
        f"background: {css_rgb(background)}; }}",
        "body { font-family: system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif; "
        f"color: {css_rgb(TEXT_COLOUR)}; }}",
        ".flash { position: fixed; display: block; margin: 0; padding: 0; border: 0; object-fit: fill; }",
        ".copy { position: fixed; overflow: hidden; }",
        ".copy h1 { font-size: 20px; margin: 0 0 8px; }",
        ".copy p { font-size: 16px; line-height: 1.4; margin: 0; }",
        ".distractor { position: fixed; overflow: hidden; }",
        f"#fade {{ background: {css_rgb(FADE_COLOUR)}; animation: bench-fade {FADE_PERIOD_S}s linear infinite; }}",
        "@keyframes bench-fade { 0% { opacity: 1; } 50% { opacity: 0; } 100% { opacity: 1; } }",
        "#pattern { background: repeating-conic-gradient(rgb(0, 0, 0) 0% 25%, rgb(255, 255, 255) 0% 50%) "
        "0 0 / 40px 40px; }",
        f"#marquee {{ white-space: nowrap; font-size: 20px; line-height: 40px; color: {css_rgb(MARQUEE_COLOUR)}; }}",
        f"#marquee span {{ display: inline-block; animation: bench-marquee "
        f"{MARQUEE_TRAVEL_PX / MARQUEE_SPEED_PX_S:g}s linear infinite; }}",
        "@keyframes bench-marquee { from { transform: translateX(400px); } to { transform: translateX(-800px); } }",
    ]
    if mechanism == "css":
        # steps(1, end) holds A for the first half of the cycle and B for the
        # second half; the animation restarts at A, exactly floor(t*2*hz) % 2.
        rules.append(f".flash {{ animation: bench-flash {period_css(rate_hz)} steps(1, end) infinite; }}")
        rules.append(f"@keyframes bench-flash {{ 0% {{ background-color: {css_rgb(colour_a)}; }} "
                     f"50% {{ background-color: {css_rgb(colour_b)}; }} "
                     f"100% {{ background-color: {css_rgb(colour_a)}; }} }}")
    return "\n".join(rules)


def flash_element(mechanism, element, colour_a, video_src=None):
    """Markup for one flashing element; its id is the culprit selector."""
    selector, rect = element["selector"], element["rect"]
    ident = selector.lstrip("#")
    common = f'id="{ident}" class="flash" data-bench-culprit'
    style = f"{rect_style(rect)};background-color:{css_rgb(colour_a)}"
    if mechanism == "canvas":
        # width/height attributes equal the CSS size: one canvas pixel per CSS pixel.
        return f'<canvas {common} width="{rect["w"]}" height="{rect["h"]}" style="{style}"></canvas>'
    if mechanism == "video":
        return (f'<video {common} autoplay muted loop playsinline preload="auto" '
                f'width="{rect["w"]}" height="{rect["h"]}" src="{video_src}" style="{style}"></video>')
    return f'<div {common} style="{style}"></div>'


def state_function_js(rate_hz):
    """JS for state(ms): 0 for colour A, 1 for colour B at page time ms."""
    return f"function state(ms) {{ return Math.floor(ms / 1000 * 2 * {rate_hz}) % 2; }}"


def mechanism_script(mechanism, colour_a, colour_b, rate_hz):
    """The inline script that drives waapi, raf and canvas pages."""
    colours = f'var A = "{css_rgb(colour_a)}", B = "{css_rgb(colour_b)}";'
    if mechanism == "waapi":
        # The step easing sits on the keyframes, not in the options: an easing in
        # the options shapes the whole iteration's progress, and steps(1, end)
        # there would hold progress at 0 so the 50% keyframe is never reached.
        # Per-keyframe easing is what CSS animation-timing-function does.
        body = f"""{colours}
  var frames = [{{backgroundColor: A, easing: "steps(1, end)"}},
                {{backgroundColor: B, offset: 0.5, easing: "steps(1, end)"}},
                {{backgroundColor: A}}];
  var options = {{duration: 1000 / {rate_hz}, iterations: Infinity}};
  var elements = document.querySelectorAll(".flash");
  for (var i = 0; i < elements.length; i++) elements[i].animate(frames, options);"""
    elif mechanism == "raf":
        body = f"""{colours}
  {state_function_js(rate_hz)}
  var elements = document.querySelectorAll(".flash");
  function tick() {{
    var colour = state(performance.now()) ? B : A;
    for (var i = 0; i < elements.length; i++) elements[i].style.backgroundColor = colour;
    requestAnimationFrame(tick);
  }}
  tick();"""
    elif mechanism == "canvas":
        body = f"""{colours}
  {state_function_js(rate_hz)}
  var canvases = document.querySelectorAll("canvas.flash");
  function tick() {{
    var colour = state(performance.now()) ? B : A;
    for (var i = 0; i < canvases.length; i++) {{
      var ctx = canvases[i].getContext("2d");
      ctx.fillStyle = colour;
      ctx.fillRect(0, 0, canvases[i].width, canvases[i].height);
    }}
    requestAnimationFrame(tick);
  }}
  tick();"""
    else:
        return ""
    return f"<script>\n(function () {{\n  {body}\n}})();\n</script>"


def distractor_markup(name, rect):
    """Markup for one distractor at its pixel rectangle."""
    style = rect_style(rect)
    if name == "marquee":
        text = ("Scrolling text moves across this strip at 120 pixels per second. It is dark text on a "
                "light background and it is not a flash. ")
        return f'<div id="marquee" class="distractor" style="{style}"><span>{text * 2}</span></div>'
    return f'<div id="{name}" class="distractor" style="{style}"></div>'


def text_markup(rect, page_id, note=None):
    """The static heading and paragraph."""
    paragraph = note or PARAGRAPH
    return (f'<div class="copy" style="{rect_style(rect)}"><h1>FlashTrace benchmark page {page_id}</h1>'
            f"<p>{paragraph}</p></div>")


def truth_script(entry):
    """The page's own truth entry, embedded for readers of the HTML source."""
    text = json.dumps(entry, indent=2).replace("</", "<\\/")
    return f'<script type="application/json" id="bench-truth">\n{text}\n</script>'


def render_page(entry, colour_a, colour_b, text_rect, distractor_rects, video_srcs=None, skipped=False):
    """Assemble the whole HTML document for one page.

    `entry` is the page's truth entry (already complete). `video_srcs` lists
    one WebM file name per element for video pages. `skipped` renders the
    visible "video unavailable" note and no animation at all.
    """
    mechanism = "none" if skipped else entry["mechanism"]
    rate_hz = entry["rateHz"]
    elements = []
    for index, element in enumerate(entry["elements"]):
        src = video_srcs[index] if video_srcs else None
        elements.append(flash_element(mechanism, element, colour_a, src))
    distractors = [distractor_markup(name, distractor_rects[name]) for name in entry["distractors"]]
    note = VIDEO_UNAVAILABLE if skipped else None
    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>FlashTrace {entry['id']}</title>",
        "<style>",
        styles(tuple(entry["background"]), mechanism, colour_a, colour_b, rate_hz),
        "</style>",
        "</head>",
        "<body>",
        text_markup(text_rect, entry["id"], note) if text_rect else "",
        *distractors,
        *elements,
        mechanism_script(mechanism, colour_a, colour_b, rate_hz),
        truth_script(entry),
        "</body>",
        "</html>",
    ]
    return "\n".join(part for part in parts if part) + "\n"
