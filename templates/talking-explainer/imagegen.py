#!/usr/bin/env python3
"""Text-to-image and image edits on RunPod Public Endpoints (managed models, pay per image, nothing to deploy).

    uv run imagegen.py --model seedream --prompt "..." --size 864x1536 --out a.png
    uv run imagegen.py --model qwen-edit --input a.png --prompt "make the LED glow" --size 864x1536 --out b.png

Models (price per image, RunPod docs 2026-11): seedream $0.027, zimage $0.005, qwen-image $0.02,
qwen-edit (Qwen Image Edit 2511) $0.02, seedream-edit $0.027.
Local inputs are uploaded to R2 for a short-lived URL (the endpoints take URLs, not files).
"""
import argparse
import os
import sys
import time
from pathlib import Path

import requests

MODELS = {
    "seedream": ("seedream-v4-t2i", "t2i"),
    "zimage": ("z-image-turbo", "t2i"),
    "qwen-image": ("qwen-image-t2i", "t2i"),
    "qwen-edit": ("qwen-image-edit-2511", "edit"),
    "seedream-edit": ("seedream-v4-edit", "edit"),
}


def toolkit_root() -> Path:
    p = Path.cwd()
    while p != p.parent:
        if (p / "tools" / "file_transfer.py").exists():
            return p
        p = p.parent
    return Path(os.environ.get("VIDEO_TOOLKIT_ROOT", "."))


def api_key(root: Path) -> str:
    key = os.environ.get("RUNPOD_API_KEY")
    if not key and (root / ".env").exists():
        for line in (root / ".env").read_text().splitlines():
            if line.startswith("RUNPOD_API_KEY="):
                key = line.split("=", 1)[1].strip()
    return key or sys.exit("RUNPOD_API_KEY not set")


def upload(root: Path, path: Path) -> str:
    sys.path.insert(0, str(root / "tools"))
    from dotenv import load_dotenv
    load_dotenv(root / ".env")
    from file_transfer import upload_to_r2
    url, _ = upload_to_r2(str(path), "talking-explainer/inputs")
    return url or sys.exit(f"R2 upload failed for {path}")


def generate(model: str, prompt: str, size: tuple[int, int], out: Path, inputs: list[Path] = (), seed: int = -1) -> dict:
    root = toolkit_root()
    slug, kind = MODELS[model]
    body = {"prompt": prompt, "size": f"{size[0]}*{size[1]}", "seed": seed, "enable_safety_checker": True}
    if kind == "edit":
        urls = [upload(root, p) for p in inputs]
        body.update({"images": urls, "image": urls[0]})
    h = {"Authorization": f"Bearer {api_key(root)}", "Content-Type": "application/json"}
    t0 = time.time()
    r = requests.post(f"https://api.runpod.ai/v2/{slug}/run", json={"input": body}, headers=h, timeout=60)
    r.raise_for_status()
    job = r.json()["id"]
    while True:
        s = requests.get(f"https://api.runpod.ai/v2/{slug}/status/{job}", headers=h, timeout=60).json()
        if s.get("status") in ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"):
            break
        if time.time() - t0 > 600:
            sys.exit(f"{slug}: no result after 600 s")
        time.sleep(2)
    if s["status"] != "COMPLETED":
        raise RuntimeError(f"{slug} {s['status']}: {s.get('error') or s}")
    o = s["output"]
    url = o.get("result") or o.get("image_url") or (o.get("images") or [None])[0]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(requests.get(url, timeout=120).content)
    return {"seconds": round(time.time() - t0, 1), "cost": o.get("cost")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=MODELS, required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--size", default="864x1536")
    ap.add_argument("--input", nargs="*", type=Path, default=[])
    ap.add_argument("--seed", type=int, default=-1)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    w, hh = (int(x) for x in a.size.lower().split("x"))
    info = generate(a.model, a.prompt, (w, hh), a.out, a.input, a.seed)
    print(f"{a.out}: {info['seconds']}s, cost {info['cost']}")


if __name__ == "__main__":
    main()
