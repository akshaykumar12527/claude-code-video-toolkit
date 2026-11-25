#!/usr/bin/env python3
"""Assemble hook.mp4, main.mp4 and final.mp4 from the recording, the scene clips and the captions.

  hook  (parts.hook)   generated clip on the top half, the presenter (face crop) on the bottom half
  main  (parts.main)   scene clips full screen, each retimed to its line; the presenter's voice under it
  cta   (parts.cta)    the presenter full screen
Captions: the script's own words (storyboard shot text, and script.md [CTA]) laid onto whisper's word timings.
A shot with no clip yet shows its start frame, so the build runs at every stage.

    uv run build.py            # out/final.mp4, out/hook.mp4, out/main.mp4 (main ends with the CTA)
    uv run build.py --hook     # only out/hook.mp4, to review the hook before the main clips exist
"""
import json
import re
import subprocess
import sys
from pathlib import Path

from moviepy import (CompositeVideoClip, ImageClip, VideoFileClip, vfx)
from PIL import Image, ImageDraw, ImageFont

HERE = Path.cwd()
W, H, FPS = 1080, 1920, 30
# Burned-in captions are off by default: the presenter adds subtitles in the app they post with.
CFG = {"faceCropY": 150, "captions": {"enabled": False, "y": 1480, "hookY": 900, "size": 62, "maxWords": 3, "maxChars": 20}}
if (HERE / "config.json").exists():
    user = json.loads((HERE / "config.json").read_text())
    CFG.update({k: v for k, v in user.items() if k != "captions"})
    CFG["captions"].update(user.get("captions", {}))

FONT_PATHS = ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]


def font(size: int):
    for p in FONT_PATHS:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def cover(clip, w: int, h: int):
    s = max(w / clip.w, h / clip.h)
    c = clip.resized(s)
    return c.cropped(x_center=c.w / 2, y_center=c.h / 2, width=w, height=h)


def pill(text: str, size: int) -> Image.Image:
    f = font(size)
    l, t, r, b = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), text, font=f)
    pad_x, pad_y = int(size * 0.45), int(size * 0.28)
    im = Image.new("RGBA", (r - l + 2 * pad_x, b - t + 2 * pad_y), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, im.width - 1, im.height - 1), radius=int(size * 0.35), fill=(0, 0, 0, 175))
    d.text((pad_x - l, pad_y - t), text, font=f, fill=(255, 255, 255, 255))
    return im


def script_section(name: str) -> str:
    txt = (HERE / "script.md").read_text() if (HERE / "script.md").exists() else ""
    m = re.search(rf"\[{name}\]\s*(.*?)(?=\n\[|\Z)", txt, re.S | re.I)
    return " ".join(m.group(1).split()) if m else ""


def caption_chunks(text: str, a: float, b: float, words: list) -> list:
    """Lay the script words of one window onto whisper's word timings for the same window."""
    sw = text.split()
    ww = [w for w in words if a - 0.05 <= (w["s"] + w["e"]) / 2 < b + 0.05]
    if not sw:
        return []
    starts = []
    for i in range(len(sw)):
        if ww:
            starts.append(max(a, ww[min(len(ww) - 1, i * len(ww) // len(sw))]["s"]))
        else:
            starts.append(a + (b - a) * i / len(sw))
    cap = CFG["captions"]
    chunks, cur, cur_start = [], [], None
    for i, word in enumerate(sw):
        if cur and (len(cur) >= cap["maxWords"] or len(" ".join(cur + [word])) > cap["maxChars"]):
            chunks.append([" ".join(cur), cur_start])
            cur, cur_start = [], None
        if cur_start is None:
            cur_start = starts[i]
        cur.append(word)
    if cur:
        chunks.append([" ".join(cur), cur_start])
    out = []
    for j, (t, s) in enumerate(chunks):
        e = chunks[j + 1][1] if j + 1 < len(chunks) else b
        if e - s > 0.05:
            out.append((t, s, min(e, b)))
    return out


def shot_visual(s: dict, w: int, h: int):
    dur = s["end"] - s["start"]
    clip_path = HERE / "clips" / f"{s['id']}.mp4"
    if clip_path.exists():
        v = VideoFileClip(str(clip_path)).without_audio()
        v = v.with_effects([vfx.MultiplySpeed(final_duration=dur)])
    else:
        v = ImageClip(str(HERE / "frames" / f"{s['id']}_start.png")).with_duration(dur)
    return cover(v, w, h).with_start(s["start"]).with_duration(dur)


def main() -> None:
    sb = json.loads((HERE / "storyboard.json").read_text())
    words = json.loads((HERE / "work" / "transcript.json").read_text())["words"]
    rec = VideoFileClip(str(HERE / sb["recording"]))
    parts = sb["parts"]
    hook_only = "--hook" in sys.argv
    end = parts["hook"]["end"] if hook_only else min(parts["cta"]["end"], rec.duration)
    layers, caps = [], []

    # hook: generated clip on top, presenter below
    hook_shots = [s for s in sb["shots"] if s["part"] == "hook"]
    hk_a, hk_b = parts["hook"]["start"], parts["hook"]["end"]
    face = rec.subclipped(hk_a, hk_b).without_audio().resized(W / rec.w)
    y0 = CFG["faceCropY"]
    face = face.cropped(x1=0, y1=y0, width=W, height=H // 2).with_start(hk_a).with_position((0, H // 2))
    layers.append(face)
    for s in hook_shots:
        layers.append(shot_visual(s, W, H // 2).with_position((0, 0)))
        caps += [(t, a, b, CFG["captions"]["hookY"]) for t, a, b in caption_chunks(s["text"], s["start"], s["end"], words)]

    # main: full-screen scene clips
    for s in (s for s in sb["shots"] if s["part"] == "main" and not hook_only):
        layers.append(shot_visual(s, W, H).with_position((0, 0)))
        caps += [(t, a, b, CFG["captions"]["y"]) for t, a, b in caption_chunks(s["text"], s["start"], s["end"], words)]

    # cta: presenter full screen
    ca = parts["cta"]["start"]
    if not hook_only:
      layers.append(cover(rec.subclipped(ca, end).without_audio(), W, H).with_start(ca))
      caps += [(t, a, b, CFG["captions"]["y"]) for t, a, b in caption_chunks(script_section("CTA"), ca, end, words)]

    if not CFG["captions"].get("enabled"):
        caps = []
    for t, a, b, y in caps:
        im = pill(t, CFG["captions"]["size"])
        path = HERE / "work" / "caps" / f"{abs(hash((t, a))) % 10**10}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        im.save(path)
        layers.append(ImageClip(str(path)).with_start(a).with_duration(b - a).with_position(("center", y - im.height // 2)))

    out = HERE / "out"
    out.mkdir(exist_ok=True)
    final = CompositeVideoClip(layers, size=(W, H)).with_duration(end).with_audio(rec.audio.subclipped(0, end))
    if hook_only:
        final.write_videofile(str(out / "hook.mp4"), fps=FPS, codec="libx264", audio_codec="aac", preset="medium",
                              ffmpeg_params=["-crf", "18", "-pix_fmt", "yuv420p"], logger=None)
        print(f"wrote {out}/hook.mp4 ({end:.1f}s) · {len(caps)} caption chunks")
        return
    final.write_videofile(str(out / "final.mp4"), fps=FPS, codec="libx264", audio_codec="aac",
                          preset="medium", ffmpeg_params=["-crf", "18", "-pix_fmt", "yuv420p"], logger=None)
    for name, a, b in (("hook", 0, parts["hook"]["end"]), ("main", parts["main"]["start"], end)):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a}", "-to", f"{b}", "-i", str(out / "final.mp4"),
                        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out / f"{name}.mp4")],
                       check=True)
    print(f"wrote {out}/final.mp4 ({end:.1f}s), hook.mp4, main.mp4 · {len(caps)} caption chunks")


if __name__ == "__main__":
    main()
