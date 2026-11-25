#!/usr/bin/env python3
"""ComfyUI on the project's GPU pod: one client and the three workflows the explainer uses.

    start frame   Qwen-Image (text to image), 8-step Lightning LoRA
    end frame     Qwen-Image-Edit (edit of the start frame), 8-step Lightning LoRA
    clip          Wan 2.2 14B first-last-frame (two fp8 experts), 4-step Lightning LoRAs

The pod is started and stopped by the agent (see README "Pod"); its URL is in review/pod.json
({"id": ..., "url": "https://<id>-8188.proxy.runpod.net"}) or the COMFY_URL environment variable.
File names below match pod_setup.sh. They were taken from the Comfy-Org repackaged repos and must be
confirmed on the first live run (`/object_info` lists what the pod's ComfyUI can load).
"""
import base64
import json
import os
import time
import uuid
from pathlib import Path

import requests

HERE = Path.cwd()

WAN = {
    "high": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
    "low": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
    "lora_high": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
    "lora_low": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
    "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "vae": "wan_2.1_vae.safetensors",
}
QWEN = {
    "t2i": "qwen_image_fp8_e4m3fn.safetensors",
    "edit": "qwen_image_edit_2509_fp8_e4m3fn.safetensors",
    "clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
    "vae": "qwen_image_vae.safetensors",
    "lora_t2i": "Qwen-Image-Lightning-8steps-V1.1.safetensors",
    "lora_edit": "Qwen-Image-Edit-2509-Lightning-8steps-V1.0-bf16.safetensors",
}
WAN_NEGATIVE = ("text, letters, watermark, logo, subtitles, blurry, low quality, jpeg artifacts, deformed hands, "
                "extra fingers, flicker, static frame, cartoon, oversaturated")
IMG_NEGATIVE = "text, letters, watermark, logo, blurry, low quality, deformed hands, extra fingers, cartoon"


def url() -> str:
    if os.environ.get("COMFY_URL"):
        return os.environ["COMFY_URL"]
    f = HERE / "review" / "pod.json"
    if f.exists():
        return json.loads(f.read_text())["url"]
    raise SystemExit("No pod: start one first (README 'Pod'), or set COMFY_URL")


class Comfy:
    def __init__(self, base: str | None = None, auth: str | None = None):
        self.url = (base or url()).rstrip("/")
        self.s = requests.Session()
        auth = auth or os.environ.get("COMFY_AUTH")
        if auth:
            self.s.headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()

    def upload(self, path: Path) -> str:
        with open(path, "rb") as f:
            r = self.s.post(f"{self.url}/upload/image", files={"image": (path.name, f, "image/png")},
                            data={"overwrite": "true"}, timeout=120)
        r.raise_for_status()
        return r.json()["name"]

    def run(self, wf: dict, timeout: int = 1500) -> dict:
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
            time.sleep(3)
        raise TimeoutError(f"no result after {timeout}s")

    def fetch_first(self, outputs: dict, dest: Path) -> None:
        item = next(v for o in outputs.values() for k in ("images", "videos", "gifs") for v in o.get(k, []))
        r = self.s.get(f"{self.url}/view", params={"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                                                   "type": item.get("type", "output")}, timeout=300)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)


def qwen_t2i(prompt: str, w: int, h: int, seed: int, steps: int = 8) -> dict:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": QWEN["t2i"], "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": QWEN["lora_t2i"], "strength_model": 1.0}},
        "3": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["2", 0], "shift": 3.1}},
        "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": QWEN["clip"], "type": "qwen_image", "device": "default"}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": QWEN["vae"]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 0], "text": prompt}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 0], "text": IMG_NEGATIVE}},
        "8": {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "9": {"class_type": "KSampler", "inputs": {"model": ["3", 0], "positive": ["6", 0], "negative": ["7", 0],
              "latent_image": ["8", 0], "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler",
              "scheduler": "simple", "denoise": 1.0}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["5", 0]}},
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": "tx/frame"}},
    }


def qwen_edit(image_name: str, instruction: str, seed: int, steps: int = 8) -> dict:
    """Edit keeps the input's size (scaled to ~1 MP for the model, the build scales back)."""
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": QWEN["edit"], "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": QWEN["lora_edit"], "strength_model": 1.0}},
        "3": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["2", 0], "shift": 3.0}},
        "4": {"class_type": "CFGNorm", "inputs": {"model": ["3", 0], "strength": 1.0}},
        "5": {"class_type": "CLIPLoader", "inputs": {"clip_name": QWEN["clip"], "type": "qwen_image", "device": "default"}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": QWEN["vae"]}},
        "7": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "8": {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["7", 0], "upscale_method": "lanczos", "megapixels": 1.0}},
        "9": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["5", 0], "prompt": instruction, "vae": ["6", 0], "image1": ["8", 0]}},
        "10": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["5", 0], "prompt": "", "vae": ["6", 0], "image1": ["8", 0]}},
        "11": {"class_type": "VAEEncode", "inputs": {"pixels": ["8", 0], "vae": ["6", 0]}},
        "12": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["9", 0], "negative": ["10", 0],
               "latent_image": ["11", 0], "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler",
               "scheduler": "simple", "denoise": 1.0}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["6", 0]}},
        "14": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": "tx/edit"}},
    }


def wan_flf(prompt: str, start: str, end: str, w: int, h: int, length: int, seed: int, steps: int = 4) -> dict:
    split = steps // 2
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": WAN["high"], "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": WAN["low"], "weight_dtype": "default"}},
        "3": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": WAN["lora_high"], "strength_model": 1.0}},
        "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["2", 0], "lora_name": WAN["lora_low"], "strength_model": 1.0}},
        "5": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["3", 0], "shift": 5.0}},
        "6": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["4", 0], "shift": 5.0}},
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": WAN["clip"], "type": "wan", "device": "default"}},
        "8": {"class_type": "VAELoader", "inputs": {"vae_name": WAN["vae"]}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["7", 0], "text": prompt}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["7", 0], "text": WAN_NEGATIVE}},
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
        "17": {"class_type": "CreateVideo", "inputs": {"images": ["16", 0], "fps": 16}},
        "18": {"class_type": "SaveVideo", "inputs": {"video": ["17", 0], "filename_prefix": "tx/clip", "format": "mp4", "codec": "h264"}},
    }


def check() -> None:
    """Print which nodes and model files the pod's ComfyUI is missing (run once after setup)."""
    c = Comfy()
    info = c.s.get(f"{c.url}/object_info", timeout=60).json()
    nodes = {n["class_type"] for wf in (qwen_t2i("x", 64, 64, 1), qwen_edit("x.png", "x", 1),
                                         wan_flf("x", "a", "b", 64, 64, 17, 1)) for n in wf.values()}
    missing = sorted(n for n in nodes if n not in info)
    print("missing nodes:", missing or "none")
    lists = {"UNETLoader": "unet_name", "LoraLoaderModelOnly": "lora_name", "CLIPLoader": "clip_name", "VAELoader": "vae_name"}
    have = {k: set(info.get(k, {}).get("input", {}).get("required", {}).get(f, [[]])[0]) for k, f in lists.items()}
    need = {"UNETLoader": [WAN["high"], WAN["low"], QWEN["t2i"], QWEN["edit"]],
            "LoraLoaderModelOnly": [WAN["lora_high"], WAN["lora_low"], QWEN["lora_t2i"], QWEN["lora_edit"]],
            "CLIPLoader": [WAN["clip"], QWEN["clip"]], "VAELoader": [WAN["vae"], QWEN["vae"]]}
    for k, files in need.items():
        for f in files:
            print(("ok      " if f in have[k] else "MISSING ") + f)


if __name__ == "__main__":
    check()
