#!/usr/bin/env python3
"""Review studio: one local page for every gate (scenes → frames → clips → final).

The page records the presenter's clicks; it never generates anything itself. Every click is appended to
review/queue.jsonl, which the agent watches (Monitor on `tail -F`). The agent rewrites the scene from the
note, runs frames.py / clips.py / build.py, and marks the work done with `studio.py done`; the page polls
review/status.json and the media folders and updates in place.

    uv run studio.py new my-topic                 # new project in projects/my-topic (then serve from there)
    uv run studio.py list                         # every talking-explainer project and its stage
    uv run studio.py serve [--port 8765]          # start the page (opens the browser)
    uv run studio.py done frames M02 M10          # agent: these shots' frames are ready for review
    uv run studio.py fail clips M07 "reason"      # agent: show an error on the card
    uv run studio.py stage clips                  # agent: move the page to a stage
    uv run studio.py show                         # print status
"""
import fcntl
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path.cwd()
REVIEW = HERE / "review"
STATUS = REVIEW / "status.json"
QUEUE = REVIEW / "queue.jsonl"
STAGES = ["script", "record", "scenes", "frames", "clips", "final", "publish"]
PUBLISH = HERE / "publish"
SEEDANCE_PER_S = 0.052  # 720p, RunPod docs 2026-11
POD_PER_HR, POD_S_PER_CLIP = 0.79, 80  # default card L40S (community, 2026-11 read); Wan 4-step 720p, estimate


@contextmanager
def locked():
    REVIEW.mkdir(exist_ok=True)
    with open(REVIEW / ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def storyboard() -> dict:
    f = HERE / "storyboard.json"
    return json.loads(f.read_text()) if f.exists() else {"title": HERE.name.replace("-", " "), "shots": []}


def load_status() -> dict:
    if STATUS.exists():
        st = json.loads(STATUS.read_text())
    else:
        st = {"stage": "scenes" if (HERE / "storyboard.json").exists() else "script", "shots": {}}
    for s in storyboard()["shots"]:
        sh = st["shots"].setdefault(s["id"], {})
        for stage in ("scenes", "frames", "clips"):
            sh.setdefault(stage, {"status": "review", "note": "", "error": ""})
        rv = s.get("review", {})
        if rv and sh["scenes"]["status"] == "review" and rv.get("status") == "approved" and not STATUS.exists():
            sh["scenes"]["status"] = "approved"
    return st


def save_status(st: dict) -> None:
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, ensure_ascii=False))
    tmp.replace(STATUS)


def enqueue(event: dict) -> None:
    event["t"] = time.strftime("%H:%M:%S")
    with open(QUEUE, "a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def mtime(p: Path):
    return int(p.stat().st_mtime) if p.exists() else None


def state() -> dict:
    sb = storyboard()
    with locked():
        st = load_status()
    shots = []
    for s in sb["shots"]:
        i = s["id"]
        shots.append({"id": i, "part": s["part"], "start": s["start"], "end": s["end"], "text": s["text"],
                      "see": s.get("see") or s.get("shot", ""), **st["shots"][i],
                      "media": {"start": mtime(HERE / "frames" / f"{i}_start.png"),
                                "end": mtime(HERE / "frames" / f"{i}_end.png"),
                                "clip": mtime(HERE / "clips" / f"{i}.mp4")}})
    todo = [s for s in sb["shots"] if not (HERE / "clips" / f"{s['id']}.mp4").exists()]
    secs = sum(min(12, max(4, -(-(s["end"] - s["start"]) // 1))) for s in todo)
    cfg = json.loads((HERE / "config.json").read_text()) if (HERE / "config.json").exists() else {}
    if cfg.get("clipEngine", "wan") == "wan":
        pod = json.loads((REVIEW / "pod.json").read_text()) if (REVIEW / "pod.json").exists() else {}
        per_hr, per_clip = pod.get("pricePerHr", POD_PER_HR), pod.get("sPerClip", POD_S_PER_CLIP)
        mins = len(todo) * per_clip / 60 + (0 if pod else 7)  # + model download when no pod is up yet
        clip_cost = f"≈ {mins:.0f} min of {pod.get('gpu', 'L40S')}, ${mins / 60 * per_hr:.2f}"
    else:
        clip_cost = f"≈ ${secs * SEEDANCE_PER_S:.2f}"
    out = {k: mtime(HERE / "out" / f"{k}.mp4") for k in ("hook", "main", "final", "final.loudnorm")}
    meta = json.loads((PUBLISH / "meta.json").read_text()) if (PUBLISH / "meta.json").exists() else {}
    thumbs = [{"name": f.name, "v": mtime(f)} for f in sorted(PUBLISH.glob("thumb_*.png"))] if PUBLISH.exists() else []
    exp = HERE / "exports"
    exports = sorted(str(f.relative_to(HERE)) for f in exp.rglob("*") if f.is_file()) if exp.exists() else []
    script = (HERE / "script.md").read_text() if (HERE / "script.md").exists() else ""
    brief = (HERE / "brief.md").read_text() if (HERE / "brief.md").exists() else ""
    rec = next((f for f in sorted((HERE / "recording").glob("source.*"))), None) if (HERE / "recording").exists() else None
    return {"title": sb.get("title", ""), "stage": st["stage"], "shots": shots, "final": st.get("final", {}),
            "script": {**st.get("script", {}), "text": script, "brief": brief,
                       "raw": (HERE / "raw.md").read_text() if (HERE / "raw.md").exists() else ""},
            "record": {**st.get("record", {}), "file": rec.name if rec else None,
                       "mb": round(rec.stat().st_size / 1e6, 1) if rec else None},
            "clipEstimate": clip_cost, "out": out,
            "publish": {**st.get("publish", {}), "meta": meta, "thumbs": thumbs, "exports": exports}}


def advance(st: dict, stage: str) -> None:
    """Move to the stage after `stage`; cards of a generating stage start as pending (the agent picks them up)."""
    nxt = STAGES[STAGES.index(stage) + 1]
    st["stage"] = nxt
    if nxt in ("frames", "clips"):
        for sh in st["shots"].values():
            if sh[nxt]["status"] != "approved":
                sh[nxt]["status"] = "pending"


def act(body: dict) -> dict:
    stage, sid, action, note = body.get("stage"), body.get("id"), body.get("action"), (body.get("note") or "").strip()
    with locked():
        st = load_status()
        if action in ("regen", "approve") and sid in st["shots"]:
            card = st["shots"][sid][stage]
            card["note"], card["error"] = note, ""
            card["status"] = "pending" if action == "regen" else "approved"
            if action == "regen" and stage == "scenes":  # the scene changes, so its frames and clip are stale
                for later in ("frames", "clips"):
                    st["shots"][sid][later]["status"] = "review"
            if action == "approve" and stage == st["stage"] and all(
                    sh[stage]["status"] == "approved" for sh in st["shots"].values()):
                advance(st, stage)  # the last card approved: move on without a separate click
                action = "approve-stage"
        elif action == "approve-stage":
            for sh in st["shots"].values():  # "Approve all" approves every card that is not being reworked
                if sh[stage]["status"] != "pending":
                    sh[stage]["status"] = "approved"
            advance(st, stage)
        elif action == "brief-request":  # explain the concept before the presenter writes anything
            st["script"] = {**st.get("script", {}), "briefStatus": "pending", "topic": (body.get("topic") or "").strip(),
                            "note": note}
        elif action == "raw-save":
            (HERE / "raw.md").write_text((body.get("text") or "").strip() + "\n")
        elif action in ("script-draft", "script-regen"):
            if action == "script-regen" and body.get("text"):  # rework the presenter's edited version, not the old one
                (HERE / "script.md").write_text(body["text"].strip() + "\n")
            st["script"] = {**st.get("script", {}), "status": "pending", "note": note, "error": ""}
            if action == "script-draft":  # the raw inputs live in raw.md; the concept topic stays as it was
                (HERE / "raw.md").write_text((body.get("topic") or "").strip() + "\n")
        elif action == "script-save":
            (HERE / "script.md").write_text((body.get("text") or "").strip() + "\n")
        elif action == "script-approve":
            if body.get("text"):
                (HERE / "script.md").write_text(body["text"].strip() + "\n")
            st["script"] = {**st.get("script", {}), "status": "approved", "error": ""}
            if STAGES.index(st["stage"]) < STAGES.index("record"):
                st["stage"] = "record"
        elif action in ("recording-uploaded", "recording-path"):
            st["record"] = {**st.get("record", {}), "status": "pending", "note": note, "error": ""}
        elif action == "final-note":
            st["final"] = {"status": "pending", "note": note}
        elif action == "final-approve":
            st["final"] = {"status": "approved", "note": note}
            st["stage"] = "publish"
            st["publish"] = {**st.get("publish", {}), "status": "pending", "note": "", "error": ""}
        elif action == "pick":  # chosen thumbnail; no agent work needed
            st["publish"] = {**st.get("publish", {}), "choice": sid}
        elif action == "publish-text":  # the presenter edited title / caption / keywords / hashtags directly
            PUBLISH.mkdir(exist_ok=True)
            meta = json.loads((PUBLISH / "meta.json").read_text()) if (PUBLISH / "meta.json").exists() else {}
            meta.update(body.get("meta") or {})
            (PUBLISH / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        elif action == "thumb-regen":  # one thumbnail, with the presenter's note
            pub = st.setdefault("publish", {})
            pub.setdefault("thumbPending", {})[sid] = note
        elif action in ("publish-regen", "export"):
            if body.get("meta"):  # unsaved edits in the boxes go into the export too
                PUBLISH.mkdir(exist_ok=True)
                meta = json.loads((PUBLISH / "meta.json").read_text()) if (PUBLISH / "meta.json").exists() else {}
                meta.update(body["meta"])
                (PUBLISH / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
            st["publish"] = {**st.get("publish", {}), "status": "pending" if action == "publish-regen" else "exporting",
                             "note": note, "error": ""}
        elif action == "goto":
            st["stage"] = stage
        save_status(st)
    if action not in ("goto", "pick", "publish-text", "script-save", "raw-save"):
        ev = {"stage": stage, "id": sid, "action": action, "note": note}
        if action in ("script-draft", "brief-request"):
            ev["topic"] = (body.get("topic") or "").strip()
        enqueue(ev)
    return {"ok": True}


def mark(stage: str, ids: list, status: str, error: str = "") -> None:
    with locked():
        st = load_status()
        if stage == "thumb":  # studio.py done thumb thumb_a.png
            pend = st.setdefault("publish", {}).setdefault("thumbPending", {})
            for i in ids:
                pend.pop(i, None)
            ids = []
        elif stage == "brief":  # studio.py done brief
            st.setdefault("script", {}).update({"briefStatus": status, "error": error})
        elif stage in ("final", "publish", "script", "record"):
            st[stage] = {**st.get(stage, {}), "status": status, "error": error}
        for i in ids:
            st["shots"][i][stage].update({"status": status, "error": error})
        save_status(st)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            b = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if path == "/api/state":
            return self._json(state())
        f = (HERE / path.lstrip("/")).resolve()
        if not re.match(r"^/(frames|clips|out|publish|exports)/[\w.\-/]+$", path) or ".." in path or not f.is_file():
            return self._json({"error": "not found"}, 404)
        size = f.stat().st_size
        rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        a, b = 0, size - 1
        if rng:  # Safari needs ranges to play video
            a = int(rng.group(1) or 0)
            b = int(rng.group(2)) if rng.group(2) else size - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(b - a + 1))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(f, "rb") as fh:
            fh.seek(a)
            left = b - a + 1
            while left > 0:
                chunk = fh.read(min(1 << 16, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def do_POST(self):
        if self.path == "/api/upload":  # the presenter's take, streamed straight to recording/source.<ext>
            name = os.path.basename(self.headers.get("X-Filename", "take.mp4"))
            ext = (Path(name).suffix or ".mp4").lower()
            (HERE / "recording").mkdir(exist_ok=True)
            for old in (HERE / "recording").glob("source.*"):
                old.unlink()
            left = int(self.headers.get("Content-Length", 0))
            with open(HERE / "recording" / f"source{ext}", "wb") as f:
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            return self._json(act({"stage": "record", "action": "recording-uploaded", "note": name}))
        if self.path != "/api/action":
            return self._json({"error": "not found"}, 404)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        return self._json(act(body))


PAGE = r"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Studio</title><style>
:root{--bg:#0B1220;--card:#111a2e;--line:#1E293B;--ink:#E2E8F0;--dim:#94A3B8;--acc:#22D3EE;--ok:#22C55E;--warn:#F59E0B;--err:#F87171}
*{box-sizing:border-box} body{background:var(--bg);color:var(--ink);font:16px/1.5 Inter,system-ui,sans-serif;margin:0}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 20px;z-index:3}
.bar{display:flex;gap:12px;align-items:center} h1{font-size:18px;margin:0;flex:1}
.steps{display:flex;gap:6px;margin-top:8px} .step{border:1px solid var(--line);border-radius:999px;padding:3px 12px;font-size:13px;color:var(--dim);cursor:pointer}
.step.on{color:#04121a;background:var(--acc);border-color:var(--acc)} .step.done{color:var(--ok);border-color:var(--ok)}
button{background:var(--acc);color:#04121a;border:0;border-radius:6px;padding:7px 12px;font-weight:600;cursor:pointer;font-size:14px}
button.ghost{background:transparent;color:var(--ink);border:1px solid var(--line)} button:disabled{opacity:.4;cursor:default}
main{max-width:980px;margin:0 auto;padding:10px 20px 80px} .info{color:var(--dim);font-size:14px}
.part{color:var(--acc);font-size:12px;letter-spacing:.08em;text-transform:uppercase;margin:22px 0 6px}
.card{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:8px;padding:12px 14px;margin:10px 0;position:relative}
.card.approved{border-left-color:var(--ok)} .card.pending{border-left-color:var(--acc)} .card.error{border-left-color:var(--err)}
.meta{display:flex;gap:10px;font-size:13px;color:var(--dim)} .id{color:var(--acc);font-weight:700;font-size:15px}
.say{color:var(--dim);font-style:italic;margin:4px 0} .see{margin:4px 0 10px}
.media{display:flex;gap:10px;margin:6px 0 10px} .media figure{margin:0;flex:1;max-width:300px} .media img,.media video{width:100%;border-radius:6px;display:block;background:#000}
figcaption{color:var(--dim);font-size:12px;text-align:center}
.row{display:flex;gap:8px;align-items:flex-start} textarea{flex:1;background:#0b1324;color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:7px 9px;font:inherit;font-size:14px;min-height:36px;resize:vertical}
.badge{font-size:12px;border-radius:999px;padding:1px 8px;margin-left:auto} .b-approved{color:var(--ok);border:1px solid var(--ok)} .b-pending{color:var(--acc);border:1px solid var(--acc)} .b-error{color:var(--err);border:1px solid var(--err)}
.spin{display:inline-block;width:12px;height:12px;border:2px solid var(--acc);border-top-color:transparent;border-radius:50%;animation:r 1s linear infinite;vertical-align:-2px;margin-right:4px}
@keyframes r{to{transform:rotate(360deg)}} .err{color:var(--err);font-size:13px} .hold{color:var(--dim);font-size:13px;padding:20px 0}
.final video{max-width:360px;width:100%;border-radius:8px}
#ov{position:fixed;inset:0;background:#0B1220e6;display:none;align-items:center;justify-content:center;z-index:9}
#ov div{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:26px 34px;font-size:18px;display:flex;gap:12px;align-items:center}
#ov .spin{width:20px;height:20px;border-width:3px}
</style></head><body>
<header><div class="bar"><h1 id="title">Studio</h1><span class="info" id="count"></span><button id="stageBtn"></button></div>
<div class="steps" id="steps"></div></header><div id="ov"><div><span class="spin"></span><span id="ovt"></span></div></div><main><p class="info" id="help"></p><div id="list"></div></main>
<script>
const NAMES={script:"⓪ Script",record:"⓵ Recording",scenes:"① Scenes",frames:"② Frames",clips:"③ Clips",final:"④ Final",publish:"⑤ Publish"},
 ORDER=["script","record","scenes","frames","clips","final","publish"];
let lastStage=null;
let S=null, view=null, notes={}, sig="";
const esc=s=>(s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function ov(t){document.getElementById("ovt").textContent=t;document.getElementById("ov").style.display=t?"flex":"none"}
let moving=false;
async function post(b){const approving=/approve/.test(b.action||"");if(approving)ov("Saving your approval…");
 await fetch("/api/action",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)});
 await poll(true);if(approving&&!moving)ov("")}
function act(stage,id,action){post({stage,id,action,note:notes[stage+id]||""}); if(action==="regen"){} }
async function poll(force){const r=await fetch("/api/state");S=await r.json();
 if(S.stage!==lastStage){  // every approval moves the page to the next step, with a short "opening" screen
  const first=lastStage===null;lastStage=S.stage;
  if(first){view=S.stage;force=true}
  else{moving=true;ov(`✅ Approved · opening ${NAMES[S.stage]}…`);
   setTimeout(()=>{view=S.stage;sig="";moving=false;ov("");render();window.scrollTo(0,0)},1200);return}}
 const s=JSON.stringify([S,view]);if(force||s!==sig){sig=s;render()}}
function help(){return {scenes:"Read each scene. Fine? Press Approve. Not fine? Write a note and press Regenerate: Claude rewrites it here.",
 frames:"Each scene's start and end picture. The clip will move from the first to the second. Approve, or note and Regenerate.",
 clips:"Each scene's clip. Press play. Approve, or note and Regenerate.",final:"The finished videos. Leave a note for changes, or approve.",
 script:"Paste your raw inputs. Claude structures them into Hook, Main and CTA. Edit here, or leave a note and Regenerate. Approve when it's ready to shoot.",
 record:"Shoot the approved script in one 9:16 take, then upload it here. Claude checks it, transcribes it and writes the scenes.",
 publish:"Pick a thumbnail, check the title, caption, keywords and hashtags (edit them directly or leave a note), then Export."}[view]}
function card(s){const st=s[view], pend=st.status==="pending", err=st.error;
 let m="";const v=`?v=${s.media.start}${s.media.end}${s.media.clip}`;
 if(view==="frames"){m=`<div class="media">${s.media.start?`<figure><img src="/frames/${s.id}_start.png${v}"><figcaption>start</figcaption></figure>`:""}${s.media.end?`<figure><img src="/frames/${s.id}_end.png${v}"><figcaption>end</figcaption></figure>`:""}</div>`}
 if(view==="clips"&&s.media.clip){m=`<div class="media"><figure><video src="/clips/${s.id}.mp4${v}" controls loop playsinline preload="metadata"></video></figure>
  ${s.media.start?`<figure><img src="/frames/${s.id}_start.png${v}"><figcaption>start</figcaption></figure>`:""}${s.media.end?`<figure><img src="/frames/${s.id}_end.png${v}"><figcaption>end</figcaption></figure>`:""}</div>`}
 const ready=view==="scenes"||(view==="frames"?s.media.start&&s.media.end:s.media.clip);
 const badge=pend?`<span class="badge b-pending"><span class="spin"></span>${st.note?"regenerating":"generating"}</span>`:err?`<span class="badge b-error">error</span>`:st.status==="approved"?`<span class="badge b-approved">approved</span>`:"";
 return `<section class="card ${pend?"pending":err?"error":st.status}"><div class="meta"><span class="id">${s.id}</span><span>${s.start.toFixed(1)}s to ${s.end.toFixed(1)}s</span>${badge}</div>
 <p class="say">“${esc(s.text)}”</p><p class="see">${esc(s.see)}</p>${m}${err?`<p class="err">${esc(err)}</p>`:""}
 ${pend?"":`<div class="row"><textarea data-k="${view+s.id}" placeholder="What should change? (optional)" oninput="notes[this.dataset.k]=this.value">${esc(notes[view+s.id]??"")}</textarea>
 <button class="ghost" onclick="act('${view}','${s.id}','regen')">↻ Regenerate</button><button ${ready?"":"disabled"} onclick="act('${view}','${s.id}','approve')">✅ Approve</button></div>`}</section>`}
function render(){document.getElementById("title").textContent=S.title;
 const cur=ORDER.indexOf(S.stage);
 document.getElementById("steps").innerHTML=ORDER.map((k,i)=>`<span class="step ${k===view?"on":i<cur?"done":""}" onclick="view='${k}';sig='';render()">${NAMES[k]}</span>`).join("");
 document.getElementById("help").textContent=help();
 const btn=document.getElementById("stageBtn");
 if(ORDER.indexOf(view)>cur){document.getElementById("list").innerHTML=`<p class="hold">This stage opens after ${NAMES[S.stage]} is approved.</p>`;btn.style.display="none";return}
 if(view==="script"){renderScript();btn.style.display="none";document.getElementById("count").textContent="";return}
 if(view==="record"){renderRecord();btn.style.display="none";document.getElementById("count").textContent="";return}
 if(view==="final"){renderFinal();btn.style.display="none";document.getElementById("count").textContent="";return}
 if(view==="publish"){renderPublish();btn.style.display="none";document.getElementById("count").textContent="";return}
 if(ORDER.indexOf(view)>cur){document.getElementById("list").innerHTML=`<p class="hold">This stage opens after ${NAMES[S.stage]} is approved.</p>`;btn.style.display="none";return}
 const a=S.shots.filter(s=>s[view].status==="approved").length,p=S.shots.filter(s=>s[view].status==="pending").length;
 document.getElementById("count").textContent=`${a}/${S.shots.length} approved${p?` · ${p} working`:""}`;
 btn.style.display=view===S.stage?"":"none";
 const nxt=ORDER[cur+1], cost=view==="frames"?` · clips ${S.clipEstimate}`:"";
 btn.textContent=p?`Wait: ${p} working`:`Approve all → ${NAMES[nxt]}${cost}`; btn.disabled=p>0;
 btn.onclick=()=>post({stage:view,action:"approve-stage"});
 const ta=document.activeElement&&document.activeElement.dataset?document.activeElement.dataset.k:null;
 let h="",part="";for(const s of S.shots){if(s.part!==part){part=s.part;h+=`<div class="part">${part==="hook"?"Hook · top half, you below":"Main · full screen"}</div>`}h+=card(s)}
 document.getElementById("list").innerHTML=h;
 if(ta){const t=document.querySelector(`textarea[data-k="${ta}"]`);if(t){t.focus();t.selectionStart=t.selectionEnd=t.value.length}}}
function renderFinal(){const f=S.final||{},v=k=>S.out[k]?`<figure><video src="/out/${k}.mp4?v=${S.out[k]}" controls playsinline></video><figcaption>${k}.mp4</figcaption></figure>`:"";
 const pend=f.status==="pending";
 document.getElementById("list").innerHTML=`<div class="card final ${pend?"pending":f.status||""}">${pend?`<p><span class="spin"></span>Building…</p>`:""}
 <div class="media">${S.out["final.loudnorm"]?v("final.loudnorm"):v("final")}${v("hook")}${v("main")}</div>
 ${(S.out.final||S.out["final.loudnorm"])?"":`<p class="hold">The videos appear here once the clips are approved and built.</p>`}
 <div class="row"><textarea data-k="final" placeholder="Changes to the finished video?" oninput="notes.final=this.value">${esc(notes.final||"")}</textarea>
 <button class="ghost" onclick="post({stage:'final',action:'final-note',note:notes.final||''})">↻ Send note</button><button onclick="post({stage:'final',action:'final-approve',note:notes.final||''})">✅ Approve</button></div></div>`}
function renderPublish(){const P=S.publish||{},m=P.meta||{},busy=P.status==="pending"||P.status==="exporting";
 const fld=(k,label,rows)=>`<div style="margin:8px 0"><div class="info" style="display:flex;align-items:center;gap:8px">${label}<button class="ghost" style="margin-left:auto;padding:3px 10px;font-size:13px" onclick="copyField('${k}',this)">📋 Copy</button></div><textarea data-k="pub-${k}" rows="${rows}" style="width:100%" oninput="notes['pub-${k}']=this.value;autosave('publish')">${esc(notes['pub-'+k]??(Array.isArray(m[k])?m[k].join(k==="hashtags"?" ":", "):(m[k]||"")))}</textarea></div>`;
 const tp=P.thumbPending||{};
 const th=(P.thumbs||[]).map(t=>{const k="thumb-"+t.name,busy1=t.name in tp;return `<div style="flex:1;max-width:300px">
  <figure style="cursor:pointer;outline:${P.choice===t.name?"3px solid var(--ok)":"1px solid var(--line)"};border-radius:8px;position:relative" onclick="post({stage:'publish',id:'${t.name}',action:'pick'})">
  <img src="/publish/${t.name}?v=${t.v}" style="${busy1?"opacity:.35":""}"><figcaption>${busy1?'<span class="spin"></span>regenerating':P.choice===t.name?"✅ chosen":"click to choose"}</figcaption></figure>
  ${busy1?"":`<textarea data-k="${k}" rows="2" style="width:100%;margin-top:6px" placeholder="Note for this thumbnail" oninput="notes['${k}']=this.value">${esc(notes[k]||"")}</textarea>
  <button class="ghost" style="width:100%;margin-top:4px" onclick="post({stage:'publish',id:'${t.name}',action:'thumb-regen',note:notes['${k}']||''});notes['${k}']=''">↻ Regenerate</button>`}</div>`}).join("");
 document.getElementById("list").innerHTML=`<div class="card ${busy?"pending":P.status||""}">
 ${busy?`<p><span class="spin"></span>${P.status==="exporting"?"Exporting…":"Preparing thumbnails and text…"}</p>`:""}${P.error?`<p class="err">${esc(P.error)}</p>`:""}
 <h3 style="margin:4px 0">Thumbnail</h3><div class="media">${th||'<p class="hold">Thumbnails appear here.</p>'}</div>
 <h3 style="margin:14px 0 0">Text</h3>${fld("title","Title (English)",2)}${fld("post","Caption · SEO keywords · hashtags (one blank line between them)",12)}
 <div class="row" style="margin:6px 0 14px"><button class="ghost" onclick="saveText()">💾 Save my text edits</button><span class="info" id="saved-publish"></span></div>
 <div class="row"><textarea data-k="pub-note" placeholder="Note for Claude (new thumbnail idea, different title tone…)" oninput="notes['pub-note']=this.value">${esc(notes['pub-note']||"")}</textarea>
 <button class="ghost" ${busy?"disabled":""} onclick="post({stage:'publish',action:'publish-regen',note:notes['pub-note']||'',meta:pubMeta()})">↻ Regenerate</button>
 <button ${busy||!P.choice?"disabled":""} onclick="post({stage:'publish',action:'export',meta:pubMeta()})">⬇ Export</button></div>
 ${(P.exports||[]).length?`<h3 style="margin:16px 0 4px">Exported</h3>${P.exports.map(f=>`<div><a style="color:var(--acc)" href="/${f}" target="_blank">${esc(f)}</a></div>`).join("")}`:""}</div>`}
function copyField(k,b){const t=document.querySelector(`textarea[data-k="pub-${k}"]`);const v=t?t.value:"";
 const done=()=>{b.textContent="✅ Copied";setTimeout(()=>b.textContent="📋 Copy",1500)};
 if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(v).then(done)}else{t.select();document.execCommand("copy");done()}}
function pubMeta(){const g=k=>notes['pub-'+k];const meta={};
 if(g("title")!==undefined)meta.title=g("title");if(g("post")!==undefined)meta.post=g("post");return meta}
function saveText(){post({stage:"publish",action:"publish-text",meta:pubMeta()})}
let saveTimers={};
function rawSave(){clearTimeout(saveTimers.raw);saveTimers.raw=setTimeout(()=>fetch("/api/action",{method:"POST",headers:{"Content-Type":"application/json"},
 body:JSON.stringify({stage:"script",action:"raw-save",text:notes.topic||""})}),1200)}
function autosave(which){clearTimeout(saveTimers[which]);const tag=document.getElementById("saved-"+which);if(tag)tag.textContent="editing…";
 saveTimers[which]=setTimeout(async()=>{
  const body=which==="script"?{stage:"script",action:"script-save",text:notes['script-text']}:{stage:"publish",action:"publish-text",meta:pubMeta()};
  await fetch("/api/action",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const t=document.getElementById("saved-"+which);if(t)t.textContent="✓ saved "+new Date().toLocaleTimeString()},1200)}
function md(t){return esc(t).replace(/^### (.*)$/gm,'<h4 style="margin:12px 0 4px;color:var(--acc)">$1</h4>').replace(/^## (.*)$/gm,'<h3 style="margin:16px 0 6px">$1</h3>')
 .replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/^- (.*)$/gm,'<div style="margin:2px 0 2px 14px">• $1</div>').replace(/\n\n/g,'<br>')}
function renderScript(){const P=S.script||{},busy=P.status==="pending",has=(P.text||"").trim().length>0;
 const bBusy=P.briefStatus==="pending",hasBrief=(P.brief||"").trim().length>0;
 const briefCard=`<div class="card ${bBusy?"pending":""}"><h3 style="margin:4px 0">1 · Understand the concept</h3>
  ${hasBrief?`<div style="max-height:520px;overflow:auto;padding-right:8px">${md(P.brief)}</div>`:""}
  ${bBusy?`<p><span class="spin"></span>Claude is writing the explanation${P.topic?` of “${esc(P.topic)}”`:""}…</p>`:`
  <div class="row" style="margin-top:8px"><textarea data-k="brief-topic" rows="1" placeholder="Topic, e.g. How does a mouse work?" oninput="notes['brief-topic']=this.value">${esc(notes['brief-topic']??(P.topic||""))}</textarea>
  <button class="ghost" onclick="post({stage:'script',action:'brief-request',topic:notes['brief-topic']??(S.script.topic||''),note:notes['brief-note']||''})">📖 ${hasBrief?"Explain again":"Explain the concept"}</button></div>
  ${hasBrief?`<div class="row" style="margin-top:6px"><textarea data-k="brief-note" rows="1" placeholder="Question or what to go deeper on (optional)" oninput="notes['brief-note']=this.value">${esc(notes['brief-note']||"")}</textarea></div>`:""}`}</div>`;
 if(!has&&!busy){document.getElementById("list").innerHTML=briefCard+`<div class="card ${P.error?"error":""}"><h3 style="margin:4px 0">2 · Your raw inputs</h3>${P.error?`<p class="err">${esc(P.error)}</p>`:""}
  <p class="info">Topic, rough notes, facts, the points you want to make, an example you like, even a rough draft in any order. Claude structures it into Hook, Main and CTA.</p>
  <textarea data-k="topic" rows="14" style="width:100%" placeholder="e.g. Wi-Fi kaise kaam karta hai. Router radio waves bhejta hai, 2.4 vs 5 GHz, deewar ke peeche signal kyun weak hota hai…" oninput="notes.topic=this.value;rawSave()">${esc(notes.topic??(S.script.raw||""))}</textarea>
  <div class="row" style="margin-top:8px"><button onclick="post({stage:'script',action:'script-draft',topic:notes.topic||''})">✍️ Structure into a script</button></div></div>`;return}
 document.getElementById("list").innerHTML=(hasBrief?`<details class="card"><summary class="info" style="cursor:pointer">The concept explanation</summary>${md(P.brief)}</details>`:"")+"<div id='scriptCard'></div>";
 document.getElementById("scriptCard").innerHTML=`<div class="card ${busy?"pending":P.status||""}">
  ${busy?`<p><span class="spin"></span>Claude is structuring your inputs into Hook, Main and CTA…</p>`:""}${P.error?`<p class="err">${esc(P.error)}</p>`:""}
  ${has?`<div class="info">Script · [Hook] [Main] [CTA] · edit freely</div><textarea data-k="script-text" rows="24" style="width:100%" ${busy?"disabled":""} oninput="notes['script-text']=this.value;autosave('script')">${esc(notes['script-text']??P.text)}</textarea>
  ${P.status==="approved"&&!busy?`<div class="row" style="margin:8px 0;align-items:center"><span class="badge b-approved" style="margin:0">✅ Approved</span>
   <span class="info">You can still edit or regenerate it${S.record&&S.record.file?" (you have already uploaded a take, so re-shoot if the words change)":""}.</span>
   <button style="margin-left:auto" onclick="view='record';sig='';render()">Go to ⓵ Recording →</button></div>`:""}
  ${busy?"":`<div class="row" style="margin:8px 0"><button class="ghost" onclick="post({stage:'script',action:'script-save',text:notes['script-text']??S.script.text})">💾 Save my edits</button><span class="info" id="saved-script"></span></div>
  <div class="row"><textarea data-k="script-note" placeholder="Note for Claude (shorter hook, simpler words, add an example…)" oninput="notes['script-note']=this.value">${esc(notes['script-note']||"")}</textarea>
  <button class="ghost" onclick="post({stage:'script',action:'script-regen',note:notes['script-note']||'',text:notes['script-text']??S.script.text});notes['script-note']='';delete notes['script-text']">↻ Regenerate</button>
  ${P.status==="approved"&&notes['script-text']===undefined?"":`<button onclick="post({stage:'script',action:'script-approve',text:notes['script-text']??S.script.text})">✅ ${P.status==="approved"?"Approve changes":"Approve script"}</button>`}</div>`}`:""}</div>`}
function renderRecord(){const R=S.record||{},busy=R.status==="pending";
 document.getElementById("list").innerHTML=`<div class="card ${busy?"pending":R.status||""}">
  ${busy?`<p><span class="spin"></span>Checking the take, transcribing it and writing the scenes…</p>`:""}${R.error?`<p class="err">${esc(R.error)}</p>`:""}
  ${R.file?`<p>Uploaded: <b>${esc(R.file)}</b> (${R.mb} MB)</p>`:""}
  <h3 style="margin:6px 0">Upload your take</h3><p class="info">One 9:16 video, the three parts in order, face in the upper middle of the frame.</p>
  <div class="row"><input type="file" id="take" accept="video/*" ${busy?"disabled":""} style="flex:1;color:var(--ink)">
  <button ${busy?"disabled":""} onclick="upload()">⬆ Upload</button></div>
  <div id="prog" class="info" style="margin-top:6px"></div>
  <details style="margin-top:14px"><summary class="info" style="cursor:pointer">Read the approved script</summary>
  <pre style="white-space:pre-wrap;font:inherit;color:var(--ink)">${esc(S.script.text||"")}</pre></details></div>`}
function upload(){const f=document.getElementById("take").files[0];if(!f)return;const x=new XMLHttpRequest();
 x.open("POST","/api/upload");x.setRequestHeader("X-Filename",f.name);
 x.upload.onprogress=e=>{if(e.lengthComputable)document.getElementById("prog").textContent=`Uploading ${Math.round(e.loaded/e.total*100)}%`};
 x.onload=()=>{document.getElementById("prog").textContent="Uploaded";poll(true)};x.send(f)}
poll(true);setInterval(()=>{const a=document.activeElement;if(!a||(a.tagName!=="TEXTAREA"&&a.tagName!=="INPUT"))poll()},2000);
</script></body></html>"""


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "serve"
    if cmd == "serve":
        port = int(args[args.index("--port") + 1]) if "--port" in args else 8765
        with locked():
            save_status(load_status())
        QUEUE.touch()
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        url = f"http://127.0.0.1:{port}/"
        print(f"Studio: {url}  (queue: {QUEUE})", flush=True)
        if "--no-open" not in args and sys.platform == "darwin":
            subprocess.run(["open", url])
        srv.serve_forever()
    elif cmd == "list":  # every talking-explainer project and where it is
        tpl = Path(__file__).resolve().parent
        root = tpl.parents[1] if (tpl.parents[1] / "projects").exists() else Path.cwd().parents[0].parent
        rows = []
        for d in sorted((root / "projects").iterdir()):
            if not (d / "studio.py").exists():
                continue
            stf = d / "review" / "status.json"
            st = json.loads(stf.read_text()) if stf.exists() else {"stage": "script"}
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime((stf if stf.exists() else d).stat().st_mtime))
            done = "exported" if (d / "exports").exists() else ""
            rows.append(f"{d.name:<28} {st.get('stage', '?'):<9} {when}  {done}")
        print(f"{'project':<28} {'stage':<9} last change")
        print("\n".join(rows) or "(none yet)")
    elif cmd == "new":  # a fresh project from this template
        import shutil
        tpl = Path(__file__).resolve().parent
        root = tpl.parents[1] if (tpl.parents[1] / "projects").exists() else Path.cwd()
        dest = root / "projects" / args[1]
        if dest.exists():
            sys.exit(f"{dest} already exists")
        dest.mkdir(parents=True)
        for f in list(tpl.glob("*.py")) + list(tpl.glob("*.sh")):
            shutil.copy(f, dest)
        # frames on the API (Seedream + Qwen Edit, the presenter's choice), clips on the GPU pod (Wan)
        (dest / "config.json").write_text(json.dumps({"cloud": "runpod", "startModel": "seedream", "endModel": "qwen-edit",
                                                      "clipEngine": "wan", "clipSize": "auto"}, indent=2) + "\n")
        print(dest)
    elif cmd in ("done", "fail", "review"):
        stage, ids = args[1], args[2:]
        err = ""
        if cmd == "fail":
            ids, err = ids[:-1], ids[-1]
        mark(stage, ids, {"done": "review", "review": "review", "fail": "review"}[cmd], err)
    elif cmd == "stage":
        with locked():
            st = load_status()
            st["stage"] = args[1]
            save_status(st)
    elif cmd == "show":
        with locked():
            st = load_status()
        print("stage:", st["stage"])
        for i, sh in st["shots"].items():
            print(i, {k: (v["status"] + (f" '{v['note']}'" if v.get("note") else "")) for k, v in sh.items()})
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
