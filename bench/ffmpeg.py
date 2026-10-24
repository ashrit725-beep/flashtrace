"""Find ffmpeg and encode the benchmark's flashing videos as WebM / VP8.

Playwright ships its own ffmpeg (ADR-010). That binary is built with
`--disable-everything` and enables only: the `image2pipe` demuxer with the
`mjpeg` decoder, the `libvpx` VP8 encoder, the `webm` muxer and a `png`
encoder. There is no `rawvideo` demuxer, so frames are fed to it as a stream
of JPEG images on stdin. Each frame is one solid colour; JPEG at quality 100
with no chroma subsampling keeps such a frame exact to within one 8-bit step,
and the VP8 encode adds about two more steps (measured on this machine:
255 -> 252, 118 -> 116 after a decode with the same ffmpeg). The truth file
flags small luminance steps on video pages as boundary cases for this reason.
"""
import glob
import io
import os
import shutil
import subprocess

from PIL import Image

FPS = 60
SECONDS = 10
JPEG_QUALITY = 100
FFMPEG_GLOBS = (
    "~/Library/Caches/ms-playwright/ffmpeg-*/ffmpeg-*",   # macOS
    "~/.cache/ms-playwright/ffmpeg-*/ffmpeg-*",           # Linux
)


def find_ffmpeg():
    """Path to an ffmpeg binary, or None.

    Order: the FLASHTRACE_FFMPEG environment variable, Playwright's cache
    (newest build when several are installed), then `ffmpeg` on PATH.
    """
    from_env = os.environ.get("FLASHTRACE_FFMPEG")
    if from_env and os.path.isfile(from_env):
        return from_env
    for pattern in FFMPEG_GLOBS:
        matches = sorted(path for path in glob.glob(os.path.expanduser(pattern))
                         if os.path.isfile(path) and os.access(path, os.X_OK))
        if matches:
            return matches[-1]
    return shutil.which("ffmpeg")


def even(value):
    """Round up to an even number of pixels; VP8 with yuv420p needs even sizes."""
    return value + (value % 2)


def frame_state(frame_index, rate_hz, fps=FPS):
    """0 for colour A, 1 for colour B at frame `frame_index`.

    This is floor(t * 2 * hz) % 2 with t = frame_index / fps, done in whole
    numbers (2 * hz is a whole number for every rate in the grid) so that no
    floating-point rounding can flip a frame at a boundary.
    """
    two_hz = round(rate_hz * 2)
    if abs(two_hz - rate_hz * 2) > 1e-9:
        raise ValueError(f"rate {rate_hz} Hz: 2 * rate must be a whole number")
    return (frame_index * two_hz // fps) % 2


def jpeg_frame(width, height, colour):
    """One solid-colour frame as JPEG bytes (quality 100, 4:4:4)."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), tuple(colour)).save(
        buffer, format="JPEG", quality=JPEG_QUALITY, subsampling=0)
    return buffer.getvalue()


def ffmpeg_command(ffmpeg, path, fps):
    """The encode command: MJPEG frames on stdin -> VP8 in WebM, bit-exact flags."""
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-r", str(fps), "-i", "pipe:0",
        "-c:v", "libvpx", "-b:v", "2M", "-deadline", "best", "-cpu-used", "0",
        "-auto-alt-ref", "0", "-pix_fmt", "yuv420p", "-g", "15",
        "-fflags", "+bitexact", "-flags", "+bitexact",
        "-threads", "1",          # one thread: no dependence on the machine's core count
        "-r", str(fps), "-f", "webm", "-y", str(path),
    ]


def encode_flash_video(ffmpeg, path, width, height, colour_a, colour_b, rate_hz,
                       fps=FPS, seconds=SECONDS):
    """Write a `seconds`-long video whose frames alternate A/B at `rate_hz`.

    `width` and `height` are the element's size in px (already even for
    cell-aligned elements; rounded up otherwise). Only two distinct JPEG
    frames exist, so they are encoded once and repeated.
    """
    width, height = even(width), even(height)
    frames = (jpeg_frame(width, height, colour_a), jpeg_frame(width, height, colour_b))
    stream = b"".join(frames[frame_state(i, rate_hz, fps)] for i in range(fps * seconds))
    run = subprocess.run(ffmpeg_command(ffmpeg, path, fps), input=stream, capture_output=True)
    if run.returncode != 0:
        detail = run.stderr.decode("utf-8", "replace").strip()[-600:]
        raise RuntimeError(f"ffmpeg failed for {path}: {detail}")
    return path
