#!/usr/bin/env python3
"""Stage 5: thumbnails, publish text and the export.

    uv run publish.py thumbs     # publish/thumbs.json → publish/thumb_<name>.png (1080x1920 covers)
    uv run publish.py thumbs b   # only thumbnail b (after a note on it)
    uv run publish.py export     # exports/<project>/: video with the chosen cover, thumbnail, caption, tags

publish/thumbs.json (written by the agent):
  [{"name": "a", "bg": "frames/H1_end.png", "text": "TV ko kaise\\n*pata chalta* hai?", "face": false}, ...]
  bg    an image from the project (a frame, a generated picture); cropped to fill
  face  true: the bottom 40% is the presenter, taken from the recording at `faceAt` seconds
  text  two lines at most; *words* in stars are drawn in the brand accent
publish/meta.json (agent writes, presenter may edit in the studio):
  title  in English
  post   one text: caption, then SEO keywords (comma separated), then hashtags, a blank line between them
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path.cwd()
PUB = HERE / "publish"
W, H = 1080, 1920
ACCENT, INK, BAND = (6, 182, 212), (255, 255, 255), (11, 18, 32)
FONTS = ["/System/Library/Fonts/Supplemental/Arial Black.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]


def font(size):
    for f in FONTS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def cover(img: Image.Image, w: int, h: int, cy: float = 0.5) -> Image.Image:
    s = max(w / img.width, h / img.height)
    im = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    x = (im.width - w) // 2
    y = min(max(round(im.height * cy - h / 2), 0), im.height - h)
    return im.crop((x, y, x + w, y + h))


def face_frame(t: float) -> Image.Image:
    sb = json.loads((HERE / "storyboard.json").read_text())
    tmp = PUB / "_face.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(HERE / sb["recording"]),
                    "-frames:v", "1", str(tmp)], check=True)
    return Image.open(tmp).convert("RGB")


def draw_text(im: Image.Image, text: str, cy: int) -> None:
    """Two lines, centred on cy, on a solid band; *word* in the accent."""
    d = ImageDraw.Draw(im)
    lines = text.split("\n")
    size = 120
    while size > 60:
        f = font(size)
        if all(d.textlength(l.replace("*", ""), font=f) <= W * 0.88 for l in lines):
            break
        size -= 6
    f = font(size)
    lh = int(size * 1.18)
    top = cy - lh * len(lines) // 2
    d.rectangle((0, top - 40, W, top + lh * len(lines) + 30), fill=BAND)
    for i, line in enumerate(lines):
        parts = re.split(r"(\*[^*]+\*)", line)
        x = (W - d.textlength(line.replace("*", ""), font=f)) / 2
        for p in parts:
            if not p:
                continue
            accent = p.startswith("*")
            p = p.strip("*")
            d.text((x, top + i * lh), p, font=f, fill=ACCENT if accent else INK)
            x += d.textlength(p, font=f)


def thumbs() -> None:
    spec = json.loads((PUB / "thumbs.json").read_text())
    only = sys.argv[2:]
    if not only:
        for old in PUB.glob("thumb_*.png"):
            old.unlink()
    for t in spec:
        if only and t["name"] not in only:
            continue
        bg = Image.open(HERE / t["bg"]).convert("RGB")
        if t.get("face"):
            im = Image.new("RGB", (W, H), BAND)
            im.paste(cover(bg, W, int(H * 0.6)), (0, 0))
            im.paste(cover(face_frame(t.get("faceAt", 1.0)), W, int(H * 0.4), cy=t.get("faceCy", 0.32)), (0, int(H * 0.6)))
            draw_text(im, t["text"], int(H * 0.6))
        else:
            im = cover(bg, W, H, cy=t.get("cy", 0.5))
            draw_text(im, t["text"], int(H * t.get("textY", 0.22)))
        out = PUB / f"thumb_{t['name']}.png"
        im.save(out)
        print(out)
    (PUB / "_face.png").unlink(missing_ok=True)


def export() -> None:
    st = json.loads((HERE / "review" / "status.json").read_text())
    choice = st.get("publish", {}).get("choice")
    if not choice:
        sys.exit("No thumbnail chosen")
    meta = json.loads((PUB / "meta.json").read_text())
    dest = HERE / "exports" / HERE.name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    thumb = dest / "thumbnail.png"
    shutil.copy(PUB / choice, thumb)
    # the final video with the thumbnail embedded as its cover image
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(HERE / "out" / "final.mp4"), "-i", str(thumb),
                    "-map", "0", "-map", "1", "-c", "copy", "-c:v:1", "png", "-disposition:v:1", "attached_pic",
                    str(dest / f"{HERE.name}.mp4")], check=True)
    for part in ("hook", "main"):
        if (HERE / "out" / f"{part}.mp4").exists():
            shutil.copy(HERE / "out" / f"{part}.mp4", dest / f"{HERE.name}_{part}.mp4")
    post = meta.get("post", "").strip()
    (dest / "caption.txt").write_text(f"{meta.get('title', '').strip()}\n\n{post}\n")
    blocks = [b.strip() for b in re.split(r"\n\s*\n", post) if b.strip()]
    tags_at = max((i for i, b in enumerate(blocks) if b.startswith("#")), default=len(blocks))
    keywords = blocks[tags_at - 1] if tags_at >= 2 else ""  # the block just above the hashtags
    (dest / "tags.txt").write_text(keywords.replace("\n", " ") + "\n")
    (dest / "metadata.json").write_text(json.dumps({**meta, "thumbnail": choice}, indent=2, ensure_ascii=False))
    for f in sorted(dest.iterdir()):
        print(f.relative_to(HERE))


if __name__ == "__main__":
    {"thumbs": thumbs, "export": export}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
