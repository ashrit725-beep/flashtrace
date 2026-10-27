"""Narrated FlashTrace demo: the slides from make_demo_video.py plus a spoken narration (macOS `say`, offline),
encoded as MP4 (H.264 + AAC). Each slide stays up at least as long as its narration. The finished frames are
checked with FlashTrace's engine. Run: .venv/bin/python video/make_demo_with_audio.py"""
import io, subprocess, sys, tempfile, wave
from pathlib import Path
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_demo_video as base                      # the same slides, colours and safety rules
import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
OUT = base.ROOT / "video" / "flashtrace-demo.mp4"
RATE, LEAD, TAIL = 44100, 0.5, 1.0                  # narration starts 0.5 s into a slide and ends 1 s before the next
NARRATION = [
    "This is FlashTrace. It checks websites for flashing that can trigger seizures, finds what causes it, and proves the fix works.",
    "For people with photosensitive epilepsy, flashing on a screen can trigger a seizure. The web accessibility rule, "
    "W C A G two point three point one, says nothing should flash more than three times in any one second over a large "
    "enough area. Most developers never test for it.",
    "The demo site opens behind a warning page first, because the test page flashes on purpose.",
    "This test page has a loading animation that strobes five times a second. Here it is shown as a still.",
    "One command audits the page. FlashTrace freezes the page's clock, records three hundred and sixty still frames, and "
    "measures every four by four pixel patch with the W3C formulas. The verdict is fail: five flashes per second, where "
    "the limit is three.",
    "The web viewer shows the verdict and explains it in one sentence.",
    "This chart shows flashes per second over time. The dashed line is the limit of three.",
    "The heatmap shows where the flashing happens, and FlashTrace names the cause: the loader.",
    "To prove it, FlashTrace records the page again with each suspect frozen. Freezing the loader is enough to stop the failing flashes.",
    "Then it suggests a CSS fix and tests it. Slowing the loader down takes it from five flashes a second, a fail, to three, a pass.",
    "FlashTrace was tested on two hundred generated pages with known answers. It got all one hundred and twenty two clear "
    "cases right, ranked the real culprit first on all fifty six failing pages, and gave the same result on every repeat "
    "run. It also audits its own screens, so the app itself never flashes.",
    "FlashTrace runs on your own computer, and every result can be reproduced. It is an analysis aid, not a compliance certification.",
]
assert len(NARRATION) == len(base.SLIDES)

def voice_args():
    ok = subprocess.run(["say", "-v", "Samantha", "-o", "/dev/null", "test"], capture_output=True).returncode == 0
    return ["-v", "Samantha"] if ok else []

def speak(text, tmp):
    aiff = Path(tmp) / f"n{abs(hash(text))}.aiff"
    subprocess.run(["say", *voice_args(), "-r", "172", "-o", str(aiff), text], check=True)
    pcm = subprocess.run([FFMPEG, "-v", "error", "-i", str(aiff), "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(pcm, dtype=np.int16)

def plan(clips):
    """Slide durations in frames: the original time, or longer when the narration needs it."""
    out = []
    for (img, secs), clip in zip(base.SLIDES, clips):
        need = LEAD + len(clip) / RATE + TAIL
        out.append((img, int(round(max(secs, need) * base.FPS))))
    return out

def frames(slides):
    for i, (img, n) in enumerate(slides):
        last = i == len(slides) - 1
        for _ in range(n - (0 if last else base.FADE)): yield img
        if not last:
            for k in range(1, base.FADE + 1): yield Image.blend(img, slides[i + 1][0], k / (base.FADE + 1))

def soundtrack(slides, clips, path):
    total = sum(n for _, n in slides) / base.FPS
    track = np.zeros(int(total * RATE) + RATE, dtype=np.float32)
    start = 0
    for (img, n), clip in zip(slides, clips):
        at = int((start / base.FPS + LEAD) * RATE)
        track[at:at + len(clip)] += clip.astype(np.float32)
        start += n
    track *= 0.89 * 32767 / max(1.0, float(np.abs(track).max()))          # peak about -1 dBFS
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(track.astype(np.int16).tobytes())

def encode(slides, wav):
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "image2pipe", "-vcodec", "mjpeg", "-framerate", str(base.FPS), "-i", "pipe:0",
           "-i", str(wav), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(OUT)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    cache = {}
    for img in frames(slides):
        key = id(img)
        if key in cache: data = cache[key]
        else:
            buf = io.BytesIO(); img.save(buf, "JPEG", quality=93); data = buf.getvalue()
            if any(img is s[0] for s in slides): cache[key] = data
        p.stdin.write(data)
    p.stdin.close(); p.wait()
    return p.returncode

if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        clips = [speak(t, tmp) for t in NARRATION]
        slides = plan(clips)
        wav = Path(tmp) / "narration.wav"; soundtrack(slides, clips, wav)
        rc = encode(slides, wav)
    secs = sum(n for _, n in slides) / base.FPS
    print("encoded", OUT, "rc", rc, f"{OUT.stat().st_size / 1e6:.1f} MB", f"{secs:.1f} s")
    sys.path.insert(0, str(base.ROOT))
    from engine import analyze
    result = analyze((np.asarray(f, dtype=np.uint8) for f in frames(slides)), fps=base.FPS, viewport=(base.W, base.H))
    print("FlashTrace safety check of the video:", result.verdict, "general max", result.general_max_rate, "red max", result.red_max_rate)
