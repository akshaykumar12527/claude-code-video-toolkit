#!/bin/sh
# Runs INSIDE the GPU pod (the agent starts it with exec-pod: `nohup sh /workspace/pod_setup.sh > /workspace/setup.log 2>&1 &`).
# Downloads the models the explainer needs into ComfyUI/models, skipping files already there, in parallel.
# Progress: /workspace/setup.log ; finished when it prints "SETUP DONE" (or "SETUP FAILED <file>").
# File names must match comfy.py (WAN, QWEN). Sizes: Wan set ~38 GB, Qwen set ~45 GB.
set -u
M=$(ls -d /workspace/runpod-slim/ComfyUI/models /workspace/ComfyUI/models /ComfyUI/models 2>/dev/null | head -1)
[ -z "$M" ] && { echo "SETUP FAILED no ComfyUI/models"; exit 1; }
HF=https://huggingface.co
W22=$HF/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files
W21=$HF/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files
QI=$HF/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files
QE=$HF/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files
QL=$HF/lightx2v/Qwen-Image-Lightning/resolve/main

FILES="
diffusion_models $W22/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors
diffusion_models $W22/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors
loras $W22/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors
loras $W22/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors
text_encoders $W21/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors
vae $W21/vae/wan_2.1_vae.safetensors
diffusion_models $QI/diffusion_models/qwen_image_fp8_e4m3fn.safetensors
diffusion_models $QE/diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors
text_encoders $QI/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors
vae $QI/vae/qwen_image_vae.safetensors
loras $QL/Qwen-Image-Lightning-8steps-V1.1.safetensors
loras $QL/Qwen-Image-Edit-2509/Qwen-Image-Edit-2509-Lightning-8steps-V1.0-bf16.safetensors
"
# check every link first, so a renamed file is reported in seconds rather than after 80 GB
echo "$FILES" | while read -r dir url; do
  [ -z "$dir" ] && continue
  code=$(curl -sIL -o /dev/null -w '%{http_code}' "$url")
  [ "$code" = "200" ] || { echo "SETUP FAILED $code $url"; exit 1; }
done || exit 1
echo "links ok"
# the downloads run in the same subshell as their `wait`, so the script only goes on once all have finished
echo "$FILES" | {
  while read -r dir url; do
    [ -z "$dir" ] && continue
    f=$M/$dir/$(basename "$url"); mkdir -p "$M/$dir"
    if [ -s "$f" ]; then echo "have $(basename "$f")"; continue; fi
    ( curl -sL -C - -o "$f.part" "$url" && mv "$f.part" "$f" && echo "got $(basename "$f")" ) &
  done
  wait
}
for d in diffusion_models loras text_encoders vae; do ls "$M/$d"/*.part 2>/dev/null && { echo "SETUP FAILED partial download in $d"; exit 1; }; done
du -sh "$M"
echo "SETUP DONE"
