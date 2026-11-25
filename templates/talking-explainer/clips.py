#!/usr/bin/env python3
"""Scene clips from each shot's start and end frame.

Engines (config.json "clipEngine"):
  wan (default)       Wan 2.2 14B first-last-frame in ComfyUI on the project's GPU pod (comfy.py; the pod's URL
                      comes from review/pod.json or COMFY_URL). GPU time only: cents a video.
  seedance            Seedance 1.5 Pro I2V on a RunPod Public Endpoint: image + last_image, 9:16 (hook 1:1),
                      4-12 s, 720p $0.052/s (480p $0.024/s). No pod. Marked deprecated by RunPod (replaced by
                      Seedance 2.5 at ~7x the price), so the Wan path below stays.

    uv run clips.py                         # every missing clip, on the pod (review/pod.json)
    uv run clips.py --only H1 --seed 7      # redo one shot
    uv run clips.py --engine seedance       # the API instead of the pod

Each storyboard shot becomes one clip that starts on frames/<id>_start.png and ends on
frames/<id>_end.png. Frame count follows the shot's length (Wan runs at 16 fps and wants 4n+1
frames, at most 81), so a 3 s line gets a 3 s clip that actually reaches its end frame.
The pod's models are set up by pod_setup.sh (README "Pod").
"""
import argparse
import base64
import json
import math
import os
import sys
import time
import uuid
from pathlib import Path

import requests

HERE = Path.cwd()
FPS = 16
SIZES = {"hook": (640, 576), "main": (480, 832)}
MODELS = {
    "high": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
    "low": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
    "lora_high": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
    "lora_low": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
    "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "vae": "wan_2.1_vae.safetensors",
}
NEGATIVE = ("text, letters, watermark, logo, subtitles, blurry, low quality, jpeg artifacts, deformed hands, "
            "extra fingers, flicker, static frame, cartoon, oversaturated")


def frames_for(seconds: float) -> int:
    n = round(seconds * FPS)
    n = max(17, min(81, n))
    return (n - 1) // 4 * 4 + 1


def workflow(prompt: str, start: str, end: str, w: int, h: int, length: int, seed: int, steps: int = 4) -> dict:
    split = steps // 2
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODELS["high"], "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": MODELS["low"], "weight_dtype": "default"}},
        "3": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": MODELS["lora_high"], "strength_model": 1.0}},
        "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["2", 0], "lora_name": MODELS["lora_low"], "strength_model": 1.0}},
        "5": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["3", 0], "shift": 5.0}},
        "6": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["4", 0], "shift": 5.0}},
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": MODELS["clip"], "type": "wan", "device": "default"}},
        "8": {"class_type": "VAELoader", "inputs": {"vae_name": MODELS["vae"]}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["7", 0], "text": prompt}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["7", 0], "text": NEGATIVE}},
        "11": {"class_type": "LoadImage", "inputs": {"image": start}},
        "12": {"class_type": "LoadImage", "inputs": {"image": end}},
        "13": {"class_type": "WanFirstLastFrameToVideo", "inputs": {
            "positive": ["9", 0], "negative": ["10", 0], "vae": ["8", 0], "width": w, "height": h,
            "length": length, "batch_size": 1, "start_image": ["11", 0], "end_image": ["12", 0]}},
        "14": {"class_type": "KSamplerAdvanced", "inputs": {
            "model": ["5", 0], "add_noise": "enable", "noise_seed": seed, "steps": steps, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "positive": ["13", 0], "negative": ["13", 1],
            "latent_image": ["13", 2], "start_at_step": 0, "end_at_step": split, "return_with_leftover_noise": "enable"}},
        "15": {"class_type": "KSamplerAdvanced", "inputs": {
            "model": ["6", 0], "add_noise": "disable", "noise_seed": seed, "steps": steps, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "positive": ["13", 0], "negative": ["13", 1],
            "latent_image": ["14", 0], "start_at_step": split, "end_at_step": 10000, "return_with_leftover_noise": "disable"}},
        "16": {"class_type": "VAEDecode", "inputs": {"samples": ["15", 0], "vae": ["8", 0]}},
        "17": {"class_type": "CreateVideo", "inputs": {"images": ["16", 0], "fps": FPS}},
        "18": {"class_type": "SaveVideo", "inputs": {"video": ["17", 0], "filename_prefix": "tx/clip", "format": "mp4", "codec": "h264"}},
    }


class Comfy:
    def __init__(self, url: str, auth: str | None):
        self.url = url.rstrip("/")
        self.s = requests.Session()
        if auth:
            self.s.headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()

    def upload(self, path: Path) -> str:
        with open(path, "rb") as f:
            r = self.s.post(f"{self.url}/upload/image", files={"image": (path.name, f, "image/png")},
                            data={"overwrite": "true"}, timeout=120)
        r.raise_for_status()
        return r.json()["name"]

    def run(self, wf: dict, timeout: int = 1200) -> dict:
        r = self.s.post(f"{self.url}/prompt", json={"prompt": wf, "client_id": uuid.uuid4().hex}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"prompt rejected: {r.text[:800]}")
        pid = r.json()["prompt_id"]
        t0 = time.time()
        while time.time() - t0 < timeout:
            h = self.s.get(f"{self.url}/history/{pid}", timeout=30).json()
            if pid in h:
                st = h[pid].get("status", {})
                if st.get("status_str") == "error":
                    raise RuntimeError(json.dumps(st.get("messages", []))[-800:])
                if st.get("completed"):
                    return h[pid]["outputs"]
            time.sleep(4)
        raise TimeoutError(f"no result after {timeout}s")

    def fetch(self, item: dict, dest: Path) -> None:
        r = self.s.get(f"{self.url}/view", params={"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                                                   "type": item.get("type", "output")}, timeout=300)
        r.raise_for_status()
        dest.write_bytes(r.content)


SEEDANCE = "seedance-v1-5-pro-i2v"


def seedance_clip(s: dict, style: str, dest: Path, seed: int, resolution: str = "720p") -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from imagegen import api_key, toolkit_root, upload
    root = toolkit_root()
    dur = min(12, max(4, math.ceil(s["end"] - s["start"])))
    body = {"prompt": f"{s['motion']}. {style}", "image": upload(root, HERE / "frames" / f"{s['id']}_start.png"),
            "last_image": upload(root, HERE / "frames" / f"{s['id']}_end.png"), "duration": dur,
            "resolution": resolution, "aspect_ratio": "1:1" if s["part"] == "hook" else "9:16",
            "camera_fixed": False, "generate_audio": False, "seed": seed}
    h = {"Authorization": f"Bearer {api_key(root)}", "Content-Type": "application/json"}
    r = requests.post(f"https://api.runpod.ai/v2/{SEEDANCE}/run", json={"input": body}, headers=h, timeout=60)
    r.raise_for_status()
    job, t0 = r.json()["id"], time.time()
    while True:
        st = requests.get(f"https://api.runpod.ai/v2/{SEEDANCE}/status/{job}", headers=h, timeout=60).json()
        if st.get("status") in ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"):
            break
        if time.time() - t0 > 1200:
            raise TimeoutError(f"{SEEDANCE}: no result after 1200 s (job {job})")
        time.sleep(5)
    if st["status"] != "COMPLETED":
        raise RuntimeError(f"{st['status']}: {st.get('error') or st}")
    o = st["output"]
    dest.write_bytes(requests.get(o.get("result") or o.get("video_url"), timeout=300).content)
    return {"seconds": round(time.time() - t0), "cost": o.get("cost"), "duration": dur}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--steps", type=int, default=4)
    cfg = json.loads((HERE / "config.json").read_text()) if (HERE / "config.json").exists() else {}
    ap.add_argument("--engine", choices=["seedance", "wan"], default=cfg.get("clipEngine", "wan"))
    ap.add_argument("--resolution", choices=["480p", "720p"], default="720p")
    args = ap.parse_args()
    # Wan size: 720p-class on a 48 GB card (default), 480p-class on 24 GB ("clipSize": "480p" in config.json)
    sizes = {"hook": (704, 640), "main": (720, 1280)} if cfg.get("clipSize", "720p") == "720p" else SIZES
    pod = None
    if args.engine == "wan":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import comfy as comfy_mod
        pod = comfy_mod.Comfy()  # review/pod.json or COMFY_URL
    sb = json.loads((HERE / "storyboard.json").read_text())
    out = HERE / "clips"
    out.mkdir(exist_ok=True)
    for i, s in enumerate(sb["shots"]):
        if s.get("source") == "camera" or (args.only and s["id"] not in args.only):
            continue
        dest = out / f"{s['id']}.mp4"
        if dest.exists() and not args.only:
            continue
        w, h = sizes[s["part"]]
        length = frames_for(s["end"] - s["start"])
        seed = args.seed if args.seed is not None else 42 + i
        t0 = time.time()
        if args.engine == "seedance":
            try:
                info = seedance_clip(s, sb["style"], dest, seed, args.resolution)
                print(f"{s['id']}: {info['duration']} s {args.resolution} in {info['seconds']} s, cost {info['cost']}", flush=True)
            except Exception as e:
                print(f"{s['id']}: FAILED {e}", flush=True)
            continue
        try:
            a = pod.upload(HERE / "frames" / f"{s['id']}_start.png")
            b = pod.upload(HERE / "frames" / f"{s['id']}_end.png")
            outputs = pod.run(comfy_mod.wan_flf(f"{s['motion']}. {sb['style']}", a, b, w, h, length, seed, args.steps))
            pod.fetch_first(outputs, dest)
            print(f"{s['id']}: {length} frames {w}x{h} in {time.time() - t0:.0f}s", flush=True)
        except Exception as e:  # keep going: one bad shot should not cost the rest
            print(f"{s['id']}: FAILED {e}", flush=True)


if __name__ == "__main__":
    main()
