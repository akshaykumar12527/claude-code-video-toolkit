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
| 6 | Start and end frames; the agent looks at every end frame and redoes broken edits | agent | `frames.py`: Seedream 4.0 + Qwen Image Edit 2511 | ~$0.05 a shot |
| 7 | **Gate 2: approve the frames** (`storyboard.html`; redo one with `frames.py --only M03`) | presenter | | |
| 8 | Hook clip, then the hook preview with captions | agent | `clips.py --only H1`, `build.py --hook` | ~$0.40 |
| 9 | **Gate 3: approve the hook** (the look and motion, before the main clips are paid for) | presenter | | |
| 10 | Main clips | agent | `clips.py`: Seedance 1.5 Pro I2V 720p | $0.052 a second |
| 11 | Build and QC: `hook.mp4`, `main.mp4`, `final.mp4`, loudness to -14 LUFS | agent | `build.py`, `tools/qc.py --render … --fix` | free |
| 12 | **Gate 4: approve the final video** (④ Final in the studio) | presenter | | |
| 13 | Publish kit: 2-3 cover thumbnails, title, caption, SEO keywords, hashtags | agent | `publish.py thumbs`, `publish/meta.json` | free |
| 14 | **Gate 5: pick a thumbnail, edit the text or leave a note** (⑤ Publish) | presenter | | |
| 15 | Export → `exports/<project>/`: the video with the cover embedded, hook and main, `thumbnail.png`, `caption.txt`, `tags.txt` | agent | `publish.py export` | free |

A 70 s video with about 12 shots costs roughly $0.60 in frames and $3 to $4 in clips at 720p
(half that at 480p). Check the RunPod balance before step 10.

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

## Clips (gate 3, then the rest)

Default: **Seedance 1.5 Pro I2V** on a RunPod Public Endpoint, `image` + `last_image`, 720p, 9:16 for
main shots and the frame's own shape for the hook, duration = the line rounded up, 4 to 12 s. The hook
came back in 75 s for $0.36 and moved exactly from its start frame to its end frame. The build retimes
each clip to its line.

RunPod marks Seedance 1.5 Pro deprecated (Seedance 2.5 replaces it at about 7x the price), so the
fallback is kept: `clips.py --engine wan` runs Wan 2.2 14B first-last-frame in ComfyUI on a GPU pod
(two fp8 experts with the 4-step Lightning LoRAs, `WanFirstLastFrameToVideo`, 16 fps). Managed Wan
endpoints take a start image only. Pod setup: the official ComfyUI template on a 24 GB card, 38 GB of
models from `Comfy-Org/Wan_2.2_ComfyUI_Repackaged` and `Comfy-Org/Wan_2.1_ComfyUI_repackaged` (names in
`clips.py` `MODELS`), so start it only for the clips and terminate it after.

## Files

```
script.md           the script, with [Hook] [Main] [CTA]
recording/          the take
work/transcript.json  whisper word timings
storyboard.json     parts, style, shots
frames/  clips/     generated images and clips
storyboard.html     gate 2 page;  compare.html when models are compared
out/                hook.mp4, main.mp4, final.mp4 (+ .loudnorm.mp4 from qc --fix)
config.json         startModel, endModel, faceCropY, captions (y, hookY, size, maxWords, maxChars)
```
