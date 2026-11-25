---
description: Talking-explainer videos in the review studio - new, resume, list, stop
---

# Explainer

Akshay's talking-explainer workflow (`templates/talking-explainer`, method in its README). One studio page
runs every step: ⓪ Script → ⓵ Recording → ① Scenes → ② Frames → ③ Clips → ④ Final → ⑤ Publish → Export.
The page records clicks only; this session does the work by watching the project's queue.

## Arguments

| Typed | Do |
|---|---|
| `/explainer` | List projects (`uv run templates/talking-explainer/studio.py list`), then ask: resume one, or start new (ask the topic) |
| `/explainer new <topic or slug>` | Make a slug (lowercase, hyphens, e.g. `how-wifi-works`), `studio.py new <slug>`, then **Open** |
| `/explainer resume <slug>` | **Open** that project |
| `/explainer list` | Print the list and stop |
| `/explainer stop` | Stop the studio server and the queue monitor, and terminate the project's pod if one is running |

## Open a project

1. Stop any studio server and queue monitor still running from this session (one studio at a time, port 8765).
2. Copy the template's current `*.py` into the project (`cp templates/talking-explainer/*.py projects/<slug>/`)
   so it has the latest studio. Never overwrite `config.json`, `storyboard.json` or anything in `review/`.
3. Start the server in the background: `cd projects/<slug> && uv run studio.py serve` (opens the browser).
4. Arm the monitor: `tail -n 0 -F projects/<slug>/review/queue.jsonl`, timeout 30 min; re-arm on expiry for as
   long as the session works on the video.
5. Read `review/status.json` (`studio.py show`) and tell the user where the video is and what they do next.
   If a stage has cards `pending` that were never finished (a crash or an earlier session), finish that work first.

## Queue events and what to do

| Event | Work | Then |
|---|---|---|
| `brief-request` (topic) | Write `brief.md`: one-line idea, how it works step by step with real figures, other parts, where it fails, misconceptions, Hinglish hook ideas, a 60 s story. Facts checked, copy-linted. | `studio.py done brief` |
| `script-draft` (raw inputs in `raw.md`) | Structure into `[Hook] [Main] [CTA]`, Roman Hinglish, keep the presenter's words, fix spelling, order for flow. Budget **~270 wpm** (his real pace): ~30 / ~270 / ~30 words. Flag inputs that don't match the topic instead of using them. | `studio.py done script` |
| `script-regen` (note, edited text already saved) | Rework `script.md` from the note (no note: tighten and strengthen). Keep the previous version in `review/`. | `studio.py done script` |
| `script-approve` | Copy `script.md` to `review/script_approved.md` | nothing |
| `recording-uploaded` | Check the take (9:16, audio, length, face position, level, mirroring); whisper `turbo`, `language="hi"`, `condition_on_previous_text=False` → `work/transcript.json`; split hook / main / CTA from the script; write `storyboard.json` (shots of 4 to 8 s, each with `text`, `see`, prompts, motion; hook end frame from the start frame) | `studio.py stage scenes`, `studio.py done record` |
| `regen` scenes / frames / clips (id, note) | Rewrite that shot from the note; frames: `frames.py --only <id>`; clips: `clips.py --only <id>`. Look at every result before marking it. | `studio.py done <stage> <id>` |
| `approve-stage` → frames | **Start the pod** (README "Pod": stock read, 48 GB card, price stated, `pod_setup.sh`, `review/pod.json`, `comfy.py` check). Then fill missing frames 2 to 3 at a time (`review/run_frames.sh`); inspect every end frame (duplicated hands or objects, edits that did nothing) and fix before marking | per shot `done frames <id>` |
| `approve-stage` → clips | Pod still up (start it again if it was stopped); clips 2 to 3 at a time (`review/run_clip.sh`, Wan on the pod); inspect them | per shot `done clips <id>` |
| `approve-stage` → final | **Terminate the pod** and read it back; remove `review/pod.json`. Then `build.py` (no burned-in captions), `tools/qc.py --render … --fix` on final, hook and main | `studio.py done final` |
| `final-note` | Fix what the note asks, rebuild | `studio.py done final` |
| `final-approve` | Write `publish/meta.json` (English title; one `post`: English caption, keywords, hashtags with blank lines between) and `publish/thumbs.json` (3 covers, 2-line hook, *accent* word); `publish.py thumbs`; copy-lint the text | `studio.py done publish` |
| `thumb-regen` (id, note) | Edit that entry in `thumbs.json`, `publish.py thumbs <name>` | `studio.py done thumb <file>` |
| `publish-regen` (note) | Rewrite text and/or thumbnails | `studio.py done publish` |
| `export` | `publish.py export` | `studio.py done publish` |

## Rules

- Nothing paid starts without the presenter's approval in the page. Frames and clips run on one pod: start it at
  ① → ②, stop it if the presenter is away more than ~30 min, terminate it when the clips are approved. Never leave a
  pod running at the end of a session (`/explainer stop` terminates it too).
- No LLM calls from the page or scripts: this session does the writing.
- No captions burned into the video. Publish text in English; scripts in Roman Hinglish.
- After a template change, copy the scripts into the open project and restart its server.
