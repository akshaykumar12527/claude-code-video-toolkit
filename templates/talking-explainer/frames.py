#!/usr/bin/env python3
"""Start and end frames for every storyboard shot, plus storyboard.html for approval.

Start frame: text-to-image (default Seedream 4.0) from the shot's start_prompt + the storyboard style.
End frame:   an edit of that start frame (default Qwen Image Edit 2511) with the shot's end_edit, so
             subject, light and style carry over and the video model has two frames that belong together.
             Default: Seedream 4.0 + Qwen Image Edit 2511 on RunPod Public Endpoints (imagegen.py), the pair the
             presenter chose on a side-by-side test. Seedream is API-only, so frames stay on the API while the clips
             run on the pod. config.json "startModel"/"endModel" also take "zimage", "qwen-image", "seedream-edit",
             "flux2", or "pod" (Qwen-Image / Qwen-Image-Edit in ComfyUI on the pod; untested against Seedream). Chosen on a side-by-side test 2026-11-25:
             Seedream read best and Qwen Edit 2511 changed only what was asked.

Run from the project directory (the toolkit's tools/ are found by walking up):
    uv run frames.py                  # every shot, skipping frames that exist
    uv run frames.py --only M03 M04   # regenerate these shots
    uv run frames.py --html           # only rebuild storyboard.html
"""
import argparse
import html
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path.cwd()
SIZES = {"hook": (864, 768), "main": (864, 1536)}  # hook fills the top half (1080x960); main is 9:16


def toolkit_root() -> Path:
    if os.environ.get("VIDEO_TOOLKIT_ROOT"):
        return Path(os.environ["VIDEO_TOOLKIT_ROOT"])
    p = HERE
    while p != p.parent:
        if (p / "tools" / "flux2.py").exists():
            return p
        p = p.parent
    sys.exit("Toolkit root not found; set VIDEO_TOOLKIT_ROOT")


def flux(root: Path, cloud: str, out: Path, prompt: str, w: int, h: int, seed: int, edit_from: Path | None = None) -> bool:
    # klein is step-distilled to 4; the RunPod handler would otherwise run edits at 50 steps (~95 s each)
    cmd = [sys.executable, str(root / "tools" / "flux2.py"), "--cloud", cloud, "--no-open", "--steps", "4",
           "--seed", str(seed), "-W", str(w), "-H", str(h), "--prompt", prompt, "--output", str(out)]
    if edit_from:
        cmd += ["--input", str(edit_from)]
    r = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    if r.returncode or not out.exists():
        print(f"  FAILED {out.name}: {(r.stderr or r.stdout)[-300:]}", flush=True)
        return False
    return True


def zoom_frame(start: Path, end: Path, z: dict) -> bool:
    """End frame for a camera move: crop the start frame around (cx, cy) to `scale` of its size and
    resize back. FLUX edits change content, not the camera, so push-ins are made this way."""
    from PIL import Image
    im = Image.open(start)
    W, H = im.size
    cw, ch = W * z.get("scale", 0.6), H * z.get("scale", 0.6)
    x0 = min(max(W * z.get("cx", 0.5) - cw / 2, 0), W - cw)
    y0 = min(max(H * z.get("cy", 0.5) - ch / 2, 0), H - ch)
    im.crop((round(x0), round(y0), round(x0 + cw), round(y0 + ch))).resize((W, H), Image.LANCZOS).save(end)
    return True


def write_html(sb: dict) -> Path:
    rows = []
    for s in sb["shots"]:
        dur = s["end"] - s["start"]
        rows.append(f"""
<section>
  <div class="meta"><b>{s['id']}</b> · {s['part']} · {s['start']:.1f}s to {s['end']:.1f}s ({dur:.1f}s)</div>
  <p class="line">“{html.escape(s['text'])}”</p>
  <div class="frames">
    <figure><img src="frames/{s['id']}_start.png"><figcaption>start</figcaption></figure>
    <figure><img src="frames/{s['id']}_end.png"><figcaption>end</figcaption></figure>
  </div>
  <p class="shot">{html.escape(s['shot'])}</p>
  <p class="motion">Motion: {html.escape(s['motion'])}</p>
</section>""")
    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{html.escape(sb['title'])}: storyboard</title>
<style>
body{{background:#0B1220;color:#E2E8F0;font:15px/1.45 Inter,system-ui,sans-serif;max-width:900px;margin:32px auto;padding:0 16px}}
h1{{font-size:24px}} section{{border-top:1px solid #1E293B;padding:20px 0}}
.meta{{color:#94A3B8}} .meta b{{color:#22D3EE;font-size:17px}} .line{{font-size:17px;color:#fff}}
.frames{{display:flex;gap:12px}} figure{{margin:0;flex:1}} img{{width:100%;border-radius:6px;display:block}}
figcaption{{color:#94A3B8;font-size:13px;text-align:center}} .shot,.motion{{color:#94A3B8;margin:6px 0}}
</style></head><body><h1>{html.escape(sb['title'])}: storyboard</h1>
<p>{len(sb['shots'])} shots. Each clip moves from its start frame to its end frame over the time of its line.</p>
{''.join(rows)}</body></html>"""
    out = HERE / "storyboard.html"
    out.write_text(page)
    return out


def pod_image(prompt: str, w: int, h: int, out: Path, seed: int, edit_from: Path | None = None) -> None:
    """Qwen-Image (or Qwen-Image-Edit from `edit_from`) in ComfyUI on the project's pod (comfy.py)."""
    import comfy
    c = comfy.Comfy()
    if edit_from is None:
        outputs = c.run(comfy.qwen_t2i(prompt, w, h, seed))
    else:
        # an edit keeps its input's shape, so bring the reference to this shot's shape first
        from PIL import Image
        ref = Image.open(edit_from).convert("RGB")
        if abs(ref.width / ref.height - w / h) > 0.02:
            s_ = max(w / ref.width, h / ref.height)
            ref = ref.resize((round(ref.width * s_), round(ref.height * s_)), Image.LANCZOS)
            x, y = (ref.width - w) // 2, (ref.height - h) // 2
            ref = ref.crop((x, y, x + w, y + h))
        tmp = HERE / "work" / f"_ref_{out.stem}.png"
        tmp.parent.mkdir(exist_ok=True)
        ref.save(tmp)
        outputs = c.run(comfy.qwen_edit(c.upload(tmp), prompt, seed))
        tmp.unlink(missing_ok=True)
    c.fetch_first(outputs, out)


def make(root: Path, cfg: dict, sb: dict, s: dict, i: int, which: str) -> str:
    frames = HERE / "frames"
    w, h = SIZES[s["part"]]
    seed = 11 + i * 7
    start, end = frames / f"{s['id']}_start.png", frames / f"{s['id']}_end.png"
    pod_start, pod_end = cfg["startModel"] == "pod", cfg["endModel"] == "pod"
    try:
        if which == "start" and s.get("start_ref"):
            # continuity: build this start frame from another shot's frame (same room, same person)
            ref, prompt = frames / f"{s['start_ref']}.png", f"{s['start_prompt']}, {sb['style']}"
            if pod_end:
                pod_image(prompt, w, h, start, seed, edit_from=ref)
            else:
                from imagegen import generate
                generate("seedream-edit", prompt, (w, h), start, inputs=[ref], seed=seed)
        elif which == "start":
            prompt = f"{s['start_prompt']}, {sb['style']}"
            if pod_start:
                pod_image(prompt, w, h, start, seed)
            elif cfg["startModel"] == "flux2":
                flux(root, cfg["cloud"], start, prompt, w, h, seed)
            else:
                from imagegen import generate
                generate(cfg["startModel"], prompt, (w, h), start, seed=seed)
        elif s.get("end_zoom"):
            zoom_frame(start, end, s["end_zoom"])
        elif s.get("end_prompt"):
            # the camera goes somewhere new (e.g. inside the TV): the end frame is its own image
            prompt = f"{s['end_prompt']}, {sb['style']}"
            if pod_start:
                pod_image(prompt, w, h, end, seed)
            else:
                from imagegen import generate
                generate(cfg["startModel"] if cfg["startModel"] != "flux2" else "seedream", prompt, (w, h), end, seed=seed)
        else:
            prompt = f"{s['end_edit']}. Keep the same scene, camera, framing, lighting and style."
            if pod_end:
                pod_image(prompt, w, h, end, seed, edit_from=start)
            elif cfg["endModel"] == "flux2":
                flux(root, cfg["cloud"], end, f"{prompt} {sb['style']}", w, h, seed, edit_from=start)
            else:
                from imagegen import generate
                generate(cfg["endModel"], prompt, (w, h), end, inputs=[start], seed=seed)
        return f"{s['id']} {which}: ok"
    except Exception as e:
        return f"{s['id']} {which}: FAILED {e}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="Shot ids to regenerate (their frames are replaced)")
    ap.add_argument("--ids", nargs="*", help="Shot ids to fill in (existing frames are kept)")
    ap.add_argument("--html", action="store_true", help="Only rebuild storyboard.html")
    args = ap.parse_args()
    cfg = {"cloud": "runpod", "startModel": "seedream", "endModel": "qwen-edit"}
    if (HERE / "config.json").exists():
        cfg.update(json.loads((HERE / "config.json").read_text()))

    sb = json.loads((HERE / "storyboard.json").read_text())
    if not args.html:
        root = toolkit_root()
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        (HERE / "frames").mkdir(exist_ok=True)
        pick = args.only or args.ids
        todo = [(i, s) for i, s in enumerate(sb["shots"]) if not pick or s["id"] in pick]
        for i, s in todo:
            if args.only:
                for k in ("start", "end"):
                    (HERE / "frames" / f"{s['id']}_{k}.png").unlink(missing_ok=True)
        for which in ("start", "end"):
            jobs = [(i, s) for i, s in todo if not (HERE / "frames" / f"{s['id']}_{which}.png").exists()
                    and (which == "start" or (HERE / "frames" / f"{s['id']}_start.png").exists())]
            with ThreadPoolExecutor(4) as ex:
                for line in ex.map(lambda a: make(root, cfg, sb, a[1], a[0], which), jobs):
                    print(line, flush=True)
    print(f"Storyboard: {write_html(sb)}")


if __name__ == "__main__":
    main()
