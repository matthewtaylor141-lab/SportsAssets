"""Assemble CDP screencast frames into a constant-frame-rate H.264 MP4.

Chromium sends a frame only when its compositor paints, so frames carry
their own timestamps; each frame is held exactly until the next one (ffmpeg
concat durations) and the result is resampled to a constant 30 fps. Nothing
is generated: a still screen is the last real frame, held.

usage: assemble.py OUT_DIR OUTPUT.mp4
"""
import json
import os
import subprocess
import sys

out, dest = sys.argv[1], sys.argv[2]
frames = json.load(open(os.path.join(out, "frames.json")))
frames = [f for f in frames if os.path.isfile(os.path.join(out, "frames", f["file"]))]
if len(frames) < 2:
    sys.exit("fewer than two frames were captured")
lst = os.path.join(out, "frames.ffconcat")
with open(lst, "w") as fh:
    fh.write("ffconcat version 1.0\n")
    for a, b in zip(frames, frames[1:]):
        fh.write("file 'frames/%s'\nduration %.4f\n" % (a["file"], max(0.001, b["t"] - a["t"])))
    fh.write("file 'frames/%s'\nduration 0.5\n" % frames[-1]["file"])
    fh.write("file 'frames/%s'\n" % frames[-1]["file"])
span = frames[-1]["t"] - frames[0]["t"]
print("frames %d over %.1f s (mean %.1f fps captured)" % (len(frames), span, len(frames) / max(span, 1e-9)))
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                "-i", os.path.basename(lst), "-vf", "fps=30,scale=1920:1080:flags=lanczos,format=yuv420p",
                "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-profile:v", "high",
                "-movflags", "+faststart", "-an", os.path.abspath(dest)], check=True, cwd=out)
