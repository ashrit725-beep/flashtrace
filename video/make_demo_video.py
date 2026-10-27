"""Build the FlashTrace demo video (1280x720, 30 fps, WebM) from real captured screenshots, then check it
with FlashTrace's own engine so the video itself never flashes. Run: .venv/bin/python video/make_demo_video.py"""
import glob, io, json, os, subprocess, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
A = ROOT / "video" / "public" / "assets"
OUT = ROOT / "video" / "flashtrace-demo.webm"
W, H, FPS, FADE = 1280, 720, 30, 18                     # 18 frames = 0.6 s crossfade
PAPER, INK, GREEN, RED, DARK = (244, 239, 228), (27, 26, 23), (31, 94, 62), (155, 34, 38), (30, 29, 26)
SERIF = "/System/Library/Fonts/Supplemental/Georgia.ttf"
SANS = "/System/Library/Fonts/Helvetica.ttc"
MONO = "/System/Library/Fonts/Menlo.ttc"
font = lambda path, size: ImageFont.truetype(path, size)
F = json.loads((A / "facts.json").read_text())

def wrap(draw, text, fnt, width):
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if draw.textlength(trial, font=fnt) <= width: line = trial
        else: lines.append(line); line = word
    return lines + [line]

def canvas():
    return Image.new("RGB", (W, H), PAPER)

def caption(img, text, y=600, size=30, color=INK):
    d = ImageDraw.Draw(img); f = font(SANS, size)
    for i, line in enumerate(wrap(d, text, f, W - 140)):
        d.text((70, y + i * (size + 10)), line, font=f, fill=color)

def shot_slide(name, text, kicker=None):
    img = canvas(); d = ImageDraw.Draw(img)
    pic = Image.open(A / name).convert("RGB")
    box_w, box_h = W - 140, 500
    s = min(box_w / pic.width, box_h / pic.height); pic = pic.resize((int(pic.width * s), int(pic.height * s)), Image.LANCZOS)
    x, y = (W - pic.width) // 2, 40
    d.rounded_rectangle((x - 3, y - 3, x + pic.width + 3, y + pic.height + 3), radius=10, fill=INK)
    img.paste(pic, (x, y))
    ty = y + pic.height + 26
    if kicker:
        d.text((70, ty), kicker.upper(), font=font(SANS, 18), fill=GREEN); ty += 30
    caption(img, text, y=ty)
    return img

def text_slide(title, lines, title_color=INK, size=34):
    img = canvas(); d = ImageDraw.Draw(img)
    d.text((70, 90), title, font=font(SERIF, 64), fill=title_color)
    y = 210
    for para in lines:
        f = font(SANS, size)
        for line in wrap(d, para, f, W - 140):
            d.text((70, y), line, font=f, fill=INK); y += size + 12
        y += 18
    return img

def title_slide():
    img = canvas(); d = ImageDraw.Draw(img)
    d.text((70, 120), "SEIZURE-SAFETY CHECKER FOR THE WEB", font=font(SANS, 22), fill=GREEN)
    f = font(SERIF, 120); d.text((70, 170), "Flash", font=f, fill=INK)
    d.text((70 + d.textlength("Flash", font=f), 170), "Trace", font=f, fill=GREEN)
    caption(img, "Checks websites for flashing that can trigger seizures, finds what causes it, and proves the fix works.", y=360, size=36)
    return img

def terminal_slide():
    img = canvas(); d = ImageDraw.Draw(img)
    d.rounded_rectangle((60, 40, W - 60, 470), radius=14, fill=DARK)
    m = font(MONO, 22); y = 70
    lines = ["$ flashtrace audit http://127.0.0.1:8765/site/ --attribute ablate --duration 6", "",
             "frames 360/360", "attributing 6/6", "proposing fixes",
             "FAIL: General flashes reached 5 per second in a 341x256 px window",
             "      at frames 0-359; WCAG 2.3.1 allows at most 3.",
             "capture: strategy waapi_step, deterministic yes, 360 frames",
             "culprits: 10, fixes: 3"]
    for line in lines:
        color = (240, 120, 110) if line.startswith(("FAIL", "      at")) else (232, 228, 218)
        if line.startswith("$"): color = (150, 210, 170)
        d.text((90, y), line, font=m, fill=color); y += 40
    caption(img, "One command audits the page. It records 360 still frames with the page's clock frozen, "
                 "measures every 4 x 4 pixel patch with the W3C formulas, and finds a FAIL.", y=505)
    return img

b = F["benchmark"]; fx = F["cli"]["fixVerified"]
SLIDES = [
    (title_slide(), 5),
    (text_slide("The problem", [
        "Flashing on a screen can trigger seizures in people with photosensitive epilepsy.",
        "The web accessibility rule WCAG 2.3.1 says nothing should flash more than 3 times in any one second "
        "over a big enough area: 25% of a 341 x 256 pixel window.",
        "Most developers never test for it. A spinner or a blinking badge looks harmless."]), 10),
    (shot_slide("demo-warning.png", "Our demo site opens behind a warning page first, because the test page flashes on purpose."), 6),
    (shot_slide("demo-site.png", "The test page has a loader that strobes 5 times a second. Here it is shown as a still."), 7),
    (terminal_slide(), 11),
    (shot_slide("ui-report-top.png", "The web viewer shows the verdict and explains it in one sentence.", "The report"), 7),
    (shot_slide("ui-timeline.png", "Flashes per second over time. The dashed line is the limit of 3 per second."), 7),
    (shot_slide("ui-heatmap.png", "The heatmap shows where it flashes, and FlashTrace names the cause: #loader."), 8),
    (shot_slide("ui-culprits.png", "To prove it, FlashTrace records the page again with each suspect frozen. "
                                   "Freezing #loader is enough to stop the failing flashes."), 9),
    (shot_slide("ui-fix-after.png", f"Then it suggests a CSS fix and tests it. Slowing the loader to 400 ms takes it from "
                                    f"{fx['before']['generalMaxRatePerSec']} flashes a second ({fx['before']['verdict']}) to "
                                    f"{fx['after']['generalMaxRatePerSec']} ({fx['after']['verdict']})."), 9),
    (text_slide("Does it work?", [
        f"Tested on 200 generated pages with known answers: {b['clearCaseAccuracy'].split(' ')[0]} clear cases right.",
        f"The real culprit ranked first on {b['top1AttributionMainPass'].split(' ')[0]} failing pages.",
        "Same frames and same report on 20/20 repeat runs.",
        f"FlashTrace also audits its own screens: {F['selfAudit']['scenarios']} scenarios, every one PASS."]), 10),
    (text_slide("FlashTrace", [
        "Runs on your own computer. Every result can be reproduced.",
        "github.com/ashrit725-beep/flashtrace",
        "Analysis aid, not a compliance certification."], title_color=GREEN), 6),
]

def frames():
    """Every frame in order: each slide held, with an 18-frame crossfade into the next."""
    for i, (img, secs) in enumerate(SLIDES):
        hold = secs * FPS - (FADE if i < len(SLIDES) - 1 else 0)
        for _ in range(hold): yield img
        if i < len(SLIDES) - 1:
            nxt = SLIDES[i + 1][0]
            for k in range(1, FADE + 1): yield Image.blend(img, nxt, k / (FADE + 1))

def ffmpeg():
    """Playwright's bundled ffmpeg build that has the VP8 encoder (same finder the benchmark uses)."""
    sys.path.insert(0, str(ROOT))
    from bench.ffmpeg import find_ffmpeg
    path = find_ffmpeg()
    if not path:
        sys.exit("no ffmpeg with the VP8 encoder found")
    return path

def encode():
    cmd = [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "image2pipe", "-vcodec", "mjpeg", "-framerate", str(FPS),
           "-i", "pipe:0", "-c:v", "libvpx", "-b:v", "3M", "-deadline", "good", "-cpu-used", "4", "-pix_fmt", "yuv420p", str(OUT)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    cache = {}
    for img in frames():
        key = id(img)
        if key not in cache or len(cache) > 64:
            buf = io.BytesIO(); img.save(buf, "JPEG", quality=93); data = buf.getvalue()
            if img in [s[0] for s in SLIDES]: cache[key] = data
        else: data = cache[key]
        p.stdin.write(data)
    p.stdin.close(); p.wait()
    return p.returncode

if __name__ == "__main__":
    rc = encode()
    print("encoded", OUT, "rc", rc, f"{OUT.stat().st_size / 1e6:.1f} MB", f"{sum(s for _, s in SLIDES)} s")
    sys.path.insert(0, str(ROOT))
    from engine import analyze
    result = analyze((np.asarray(f, dtype=np.uint8) for f in frames()), fps=FPS, viewport=(W, H))
    print("FlashTrace safety check of the video:", result.verdict, "general max", result.general_max_rate, "red max", result.red_max_rate)
