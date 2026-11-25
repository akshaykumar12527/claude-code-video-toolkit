# talking-explainer

A 9:16 explainer narrated by the presenter on camera. One recording in, three videos out:

```
hook.mp4    generated clip on the top half, the presenter on the bottom half
main.mp4    generated scene clips full screen under the presenter's voice, then the CTA on camera
final.mp4   hook + main
```

Every visual is an image-to-video clip that moves from a start frame to an end frame, timed to the line
it illustrates. The storyboard comes from the recording's real timing, not from estimates, and nothing
that costs money runs until the presenter has approved what it will make.

## The workflow

| # | Step | Who | Tool | Cost |
|---|------|-----|------|------|
| 1 | Presenter picks the topic; agent drafts the script (`[Hook]`, `[Main]`, `[CTA]`, Roman Hinglish) → presenter edits and **approves** → `script.md` | agent drafts, presenter decides | | free |
| 2 | Presenter shoots the approved script (one 9:16 take) and shares the file → `recording/source.mp4` | presenter | | |
| 3 | Transcribe, split into hook / main / CTA | agent | whisper `turbo`, local | free |
| 4 | **Scene list**: one line per shot (time, the words, what we see at the start, what changes by the end, the motion) | agent, or the presenter writes any scene | `storyboard.json` | free |
| 5 | **Gate 1: approve the scenes** in the studio (✅ or a note + Regenerate per scene) | presenter | `studio.py` | |
| 6 | Start the pod (README "Pod"), then start and end frames; the agent looks at every end frame and redoes broken edits | agent | `frames.py`: Qwen-Image + Qwen-Image-Edit on the pod | pod time |
| 7 | **Gate 2: approve the frames** (`storyboard.html`; redo one with `frames.py --only M03`) | presenter | | |
| 8 | Hook clip, then the hook preview | agent | `clips.py --only H1`, `build.py --hook` | pod time |
| 9 | **Gate 3: approve the hook** (the look and motion, before the main clips are paid for) | presenter | | |
| 10 | Main clips, then **terminate the pod** | agent | `clips.py`: Wan 2.2 first-last-frame on the pod | pod time |
| 11 | Build and QC: `hook.mp4`, `main.mp4`, `final.mp4`, loudness to -14 LUFS | agent | `build.py`, `tools/qc.py --render … --fix` | free |
| 12 | **Gate 4: approve the final video** (④ Final in the studio) | presenter | | |
| 13 | Publish kit: 2-3 cover thumbnails, title, caption, SEO keywords, hashtags | agent | `publish.py thumbs`, `publish/meta.json` | free |
| 14 | **Gate 5: pick a thumbnail, edit the text or leave a note** (⑤ Publish) | presenter | | |
| 15 | Export → `exports/<project>/`: the video with the cover embedded, hook and main, `thumbnail.png`, `caption.txt`, `tags.txt` | agent | `publish.py export` | free |

Frames and clips run on one GPU pod that the agent starts before the frames and terminates after the
clips. Expected: about an hour of a 48 GB card (~$0.59/hr): ~10 min of model download, ~10 min of frames,
~35 min of clips, plus the time the presenter spends reviewing while it runs. That is under $1 a video,
against ~$5 on the APIs (Seedream + Qwen Edit + Seedance, measured 2026-11-25), which stay available in
`config.json`. **Not yet run end to end on a live pod:** the first video on it is also its test.

## Starting a video

`uv run templates/talking-explainer/studio.py new <slug>` makes `projects/<slug>`; run `uv run studio.py serve`
there. The studio opens on ⓪ Script and walks through every step: each approval opens the next step on
its own (the last card approved in a stage moves the page to the next stage).

```
⓪ Script → ⓵ Recording → ① Scenes → ② Frames → ③ Clips → ④ Final → ⑤ Publish → Export
```

## Step 1: the script (⓪ Script)

**First, the concept.** The presenter types the topic and presses 📖 Explain the concept. The agent writes
`brief.md`: the idea in one line, how it works step by step with real figures, the other parts, where it
fails and why, common misconceptions, hook ideas and a suggested 60-second story. The presenter reads it
(and can ask for more depth), then writes the raw inputs from their own understanding.

Then the presenter pastes raw inputs: the topic, rough notes, facts, the points to make, an example, even a
rough draft, in any order and language. The agent structures them into the script and the presenter
edits it in the page (💾 Save), or leaves a note and Regenerates, then approves. Nothing else starts
before that approval.

- Three sections: `[Hook]` (a question or surprise in about 6 s), `[Main]` (the explanation, about 60 s),
  `[CTA]` (like, share, follow, about 6 s). Roughly 150 words a minute of speech.
- Written in Roman Hinglish, the way the presenter speaks; technical terms stay in English. Keep the
  presenter's own phrasing from the raw inputs where it works.
- Check facts before the draft goes to the presenter, and mark anything uncertain.
- Run the draft through `tools/copy_lint.py`. The approved text is `script.md`.

## Step 2: shooting

After approving the script, the page opens ⓵ Recording. The presenter shoots it and uploads the take
there (it streams to `recording/source.<ext>`). The agent checks it before anything else: 9:16, has audio,
length, the face position for the hook crop, and the audio level.

- Frame the face in the upper-middle of the picture: the hook crops the presenter to the bottom half
  from `faceCropY` (150 px down in a 1080x1920 take).
- One take, the three sections in order. The split is found from the script, so no clapper is needed.
- A lav mic recorded at -31 LUFS on the first video. `qc.py --fix` brings it to the -14 target, but a
  little more gain at the source gives cleaner sound.

## Transcript and captions

**No burned-in captions by default.** The presenter adds subtitles in the app they post with, so `build.py`
leaves the video clean. `config.json` `"captions": {"enabled": true}` turns them back on.

- Hinglish is heard as Hindi: transcribe with `language="hi"`, `condition_on_previous_text=False`
  (without it whisper `small` stopped after 22 s of a 71 s take). Only the timing is used.
- Captions are the script's own words in Roman Hinglish (shot `text` in `storyboard.json`, `script.md`
  `[CTA]`), laid onto whisper's word times for the same span.
- Shot boundaries sit on whisper segment edges and are contiguous, so no line is left without a picture.

## Step 3 onward: the studio (all gates on one page)

`uv run studio.py serve` opens http://127.0.0.1:8765, one page with four stages:
① Scenes → ② Frames → ③ Clips → ④ Final. Each card shows the time, the line and one plain sentence of
what the viewer sees, then the frames, then the clip playing in the browser. The presenter approves a
card or writes a note and presses Regenerate; "Approve all" moves to the next stage (the Clips step shows
the cost first).

The page generates nothing and calls no model. Every click is appended to `review/queue.jsonl`; the agent
watches it (Monitor on `tail -n 0 -F review/queue.jsonl`, re-armed every 30 min), rewrites the scene from
the note itself, runs `frames.py` / `clips.py` / `build.py`, and marks the card with
`studio.py done <stage> <ids>` (or `fail … "reason"`). The page polls `review/status.json` and the media
folders and updates in place, with a spinner while a card is being worked on. `scenes.py` is the older
static page for when no agent session is running.

- **Plan shots of 4 to 8 seconds.** Seedance bills at least 4 s a clip and makes up to 12, so a 2.5 s
  shot costs the same as a 4 s one. Merge short lines into one shot with one idea.
- Each shot: `start_prompt` (what we see), `end_edit` (what changes), `motion` (how it moves, camera
  included). Keep the change between start and end to one thing a viewer can follow.
- Show the list as a table before any frame is made. The presenter may rewrite any scene in plain words
  (the TV-remote hook came from the presenter: a man on a sofa raises the remote and the volume bar fills).
- Avoid runs of similar shots (five circuit-board macros in a row read as one).

## Frames (gate 2)

Start frames come from Seedream 4.0 ($0.027) and end frames are Qwen Image Edit 2511 edits of them
($0.02). On a side-by-side test Seedream read as film stills where FLUX.2 klein was murky and
Z-Image Turbo flat (with gibberish text in the picture), and Qwen Edit changed only what was asked.
`config.json` `startModel` / `endModel` switch models (`zimage`, `qwen-image`, `seedream-edit`, `flux2`).

- **Look at every end frame before showing it.** Moving a hand that holds something can leave a second
  hand and a second object behind. Name what moves and what is now empty ("his right hand holding the
  remote rises; his lap is now empty"), and try two seeds.
- Edits change content, not the camera. A camera move goes in `motion`; a push-in can also be an
  `end_zoom` (`{"cx": 0.55, "cy": 0.72, "scale": 0.5}` crops the start frame and scales it back up).
- On-screen UI the model draws (a volume bar) needs its shape spelled out: "a thin white horizontal
  slider with a speaker icon, eighty percent filled", or it draws an equaliser.
- Sizes: hook 864x768 (the 1080x960 top half), main 864x1536.

## Pod (default for frames and clips)

One RunPod GPU pod runs ComfyUI with three models: Qwen-Image (start frames), Qwen-Image-Edit (end
frames) and Wan 2.2 14B first-last-frame (clips). The workflows are in `comfy.py`; the page and the
scripts call no model API. The agent drives the pod with the RunPod MCP tools:

1. **When:** the presenter approves the scenes (① → ②). Read GPU stock first.
2. **Card:** 48 GB (A40, L40S, RTX 6000 Ada, RTX A6000), cheapest in stock. Qwen-Image is 20 GB of
   weights, so a 24 GB card works only slowly and Wan must drop to 480p (`"clipSize": "480p"`). The
   RTX 4090 ran out twice on 2026-11-25: always have a second card in mind.
3. **Create:** the official ComfyUI pod template matching the card's CUDA (CUDA 12.8 `cw3nka7d08` or the
   current 13.x one), persistent volume **120 GB** at `/workspace`, container disk 40 GB, port `8188/http`
   only, no SSH or Jupyter. State the hourly price before creating.
4. **Set up:** copy `pod_setup.sh` into `/workspace` and run it in the background with exec-pod
   (`nohup sh /workspace/pod_setup.sh > /workspace/setup.log 2>&1 &`). It checks all 12 links first,
   then downloads ~91 GB in parallel, skipping files already there; wait for `SETUP DONE` in the log.
5. **Connect:** write `review/pod.json` `{"id": "<podId>", "url": "https://<podId>-8188.proxy.runpod.net"}`,
   wait for ComfyUI to answer, then `uv run comfy.py` must print no missing nodes or files.
6. **Run:** `frames.py` and `clips.py` use the pod through `comfy.py`, 2 to 3 shots at a time.
7. **Stop:** terminate the pod as soon as the clips are approved, and read it back to confirm. If the
   presenter leaves the review for more than ~30 minutes, **stop** it (the volume stays, compute billing
   ends) and start it again when they return.

The proxy URL is public while the pod runs, so keep the pod's life short. A network volume would keep
the 91 GB between videos (no download, small monthly storage fee); that is the next optimisation.

All 12 model links were checked on 2026-11-25 (Comfy-Org repackaged repos and lightx2v Lightning LoRAs):
Wan set 38 GB, Qwen set 53 GB.

## Clips (gate 3, then the rest)

Default: **Wan 2.2 14B first-last-frame** on the pod (`clips.py`, `comfy.wan_flf`): two fp8 experts with
the 4-step Lightning LoRAs, 16 fps, frames = the line's length rounded to 4n+1 (at most 81, about 5 s),
720x1280 for main shots and 704x640 for the hook on a 48 GB card. The build retimes each clip to its line.

Fallback: `"clipEngine": "seedance"` uses Seedance 1.5 Pro I2V on a RunPod Public Endpoint (`image` +
`last_image`, 720p, $0.052 a second, 4 s minimum). The hook came back in 75 s for $0.36 and moved exactly
from its start frame to its end frame. RunPod marks it deprecated (Seedance 2.5 replaces it at about 7x
the price). Managed Wan endpoints take a start image only, which is why Wan runs on the pod.

## Files

```
script.md           the script, with [Hook] [Main] [CTA]
recording/          the take
work/transcript.json  whisper word timings
storyboard.json     parts, style, shots
frames/  clips/     generated images and clips
storyboard.html     gate 2 page;  compare.html when models are compared
out/                hook.mp4, main.mp4, final.mp4 (+ .loudnorm.mp4 from qc --fix)
config.json         startModel, endModel (pod), clipEngine (wan), clipSize (720p), faceCropY, captions
review/pod.json     the running pod's id and ComfyUI URL (written by the agent, removed when it ends)
```
