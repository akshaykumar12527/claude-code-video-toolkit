#!/usr/bin/env python3
"""Gate 1: the scene list as a simple review page, and reading the presenter's notes back.

    uv run scenes.py                                            # writes scenes.html, opens it
    uv run scenes.py --apply ~/Downloads/scenes-approved.json   # records approvals and notes in storyboard.json

Each scene shows its time, the line, and one plain sentence of what the viewer sees (`see` in
storyboard.json). The presenter approves it or leaves a note; the prompts stay the agent's job.
Notes autosave in the browser; Save downloads scenes-approved.json.
"""
import argparse
import hashlib
import html
import json
import subprocess
import sys
from pathlib import Path

HERE = Path.cwd()


def page(sb: dict) -> str:
    data = [{"id": s["id"], "part": s["part"], "start": s["start"], "end": s["end"], "text": s["text"],
             "see": s.get("see") or s.get("shot", ""), "revised": bool(s.get("revised")),
             "was": s.get("review", {}).get("note", "")} for s in sb["shots"]]
    title = html.escape(sb.get("title", "Scenes"))
    # saved notes belong to this version of the list: a revised list opens fresh
    version = hashlib.sha1("".join(d["see"] for d in data).encode()).hexdigest()[:8]
    key = json.dumps(f"scenes:{sb.get('title', '')}:{version}")
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}: scenes</title>
<style>
:root{{--bg:#0B1220;--card:#111a2e;--line:#1E293B;--ink:#E2E8F0;--dim:#94A3B8;--acc:#22D3EE;--ok:#22C55E;--warn:#F59E0B}}
*{{box-sizing:border-box}} body{{background:var(--bg);color:var(--ink);font:16px/1.5 Inter,system-ui,sans-serif;margin:0}}
header{{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 20px;display:flex;gap:12px;align-items:center;z-index:2}}
header h1{{font-size:18px;margin:0;flex:1}} .count{{color:var(--dim);font-size:14px}}
button{{background:var(--acc);color:#04121a;border:0;border-radius:6px;padding:8px 14px;font-weight:600;cursor:pointer;font-size:14px}}
button.ghost{{background:transparent;color:var(--ink);border:1px solid var(--line)}}
main{{max-width:760px;margin:0 auto;padding:12px 20px 60px}} .intro{{color:var(--dim);font-size:14px}}
.part{{color:var(--acc);font-size:12px;letter-spacing:.08em;text-transform:uppercase;margin:24px 0 6px}}
.scene{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:8px;padding:12px 14px;margin:10px 0}}
.scene.ok{{border-left-color:var(--ok)}} .scene.note{{border-left-color:var(--warn)}}
.meta{{display:flex;gap:10px;align-items:baseline;font-size:13px;color:var(--dim)}} .id{{color:var(--acc);font-weight:700;font-size:15px}}
.say{{color:var(--dim);font-style:italic;margin:4px 0}} .upd{{color:var(--warn);font-size:13px;margin:2px 0}} .see{{margin:4px 0 10px;font-size:16px}}
.row{{display:flex;gap:8px;align-items:flex-start}} .ok-btn{{background:transparent;color:var(--dim);border:1px solid var(--line);white-space:nowrap}}
.scene.ok .ok-btn{{color:var(--ok);border-color:var(--ok)}}
textarea{{flex:1;background:#0b1324;color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:7px 9px;font:inherit;font-size:14px;resize:vertical;min-height:36px}}
textarea:focus{{outline:1px solid var(--warn)}}
</style></head><body>
<header><h1>{title}</h1><span class="count" id="count"></span>
<button class="ghost" onclick="approveRest()">Approve the rest</button><button onclick="save()">Save</button></header>
<main><p class="intro">Read what each scene shows. If it's fine, press ✅. If not, write what you want in the
note. A scene without a note counts as fine. Then press <b>Save</b> and tell Claude.</p><div id="list"></div></main>
<script>
const DATA={json.dumps(data, ensure_ascii=False)}; const KEY={key};
let st=JSON.parse(localStorage.getItem(KEY)||"{{}}");
const esc=s=>(s||"").replace(/[&<>]/g,c=>({{"&":"&amp;","<":"&lt;",">":"&gt;"}}[c]));
function cls(id){{const e=st[id]||{{}};return e.note&&e.note.trim()?"note":e.ok?"ok":""}}
function render(){{let h="",part="";
 for(const s of DATA){{const e=st[s.id]||{{}};
  if(s.part!==part){{part=s.part;h+=`<div class="part">${{part==="hook"?"Hook · top half, you below":"Main · full screen"}}</div>`}}
  h+=`<section class="scene ${{cls(s.id)}}" id="s-${{s.id}}"><div class="meta"><span class="id">${{s.id}}</span><span>${{s.start.toFixed(1)}}s to ${{s.end.toFixed(1)}}s</span></div>
  <p class="say">“${{esc(s.text)}}”</p>${{s.revised?`<p class="upd">Updated from your note: “${{esc(s.was)}}”</p>`:""}}<p class="see">${{esc(s.see)}}</p>
  <div class="row"><button class="ok-btn" onclick="ok('${{s.id}}')">✅ Fine</button>
  <textarea placeholder="Note, if you want something different" oninput="note('${{s.id}}',this)">${{esc(e.note||"")}}</textarea></div></section>`}}
 document.getElementById("list").innerHTML=h;count()}}
function persist(){{localStorage.setItem(KEY,JSON.stringify(st))}}
function ok(id){{st[id]=st[id]||{{}};st[id].ok=!st[id].ok;persist();document.getElementById("s-"+id).className="scene "+cls(id);count()}}
function note(id,t){{st[id]=st[id]||{{}};st[id].note=t.value;persist();document.getElementById("s-"+id).className="scene "+cls(id);count()}}
function approveRest(){{for(const s of DATA){{st[s.id]=st[s.id]||{{}};if(!(st[s.id].note||"").trim())st[s.id].ok=true}}persist();render()}}
function count(){{const n=DATA.filter(s=>cls(s.id)==="note").length,o=DATA.filter(s=>cls(s.id)==="ok").length;
 document.getElementById("count").textContent=`${{o}} fine · ${{n}} with notes · ${{DATA.length-o-n}} to read`}}
function save(){{const out={{title:{json.dumps(sb.get("title", ""))},scenes:DATA.map(s=>{{const e=st[s.id]||{{}};const n=(e.note||"").trim();
 return {{id:s.id,status:n?"change":"approved",note:n}}}})}};
 const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],{{type:"application/json"}}));
 a.download="scenes-approved.json";a.click()}}
render();
</script></body></html>"""


def apply(path: Path) -> None:
    sb = json.loads((HERE / "storyboard.json").read_text())
    edits = {e["id"]: e for e in json.loads(path.read_text())["scenes"]}
    for s in sb["shots"]:
        e = edits.get(s["id"])
        if e:
            s["review"] = {"status": e.get("status", "open"), "note": e.get("note", "")}
            if e.get("note"):
                print(f"{s['id']}: {e['note']}")
    (HERE / "storyboard.json").write_text(json.dumps(sb, indent=2, ensure_ascii=False))
    for s in sb["shots"]:  # no note means fine: the presenter only writes where something should change
        if s.get("review", {}).get("status") == "open":
            s["review"]["status"] = "approved"
    (HERE / "storyboard.json").write_text(json.dumps(sb, indent=2, ensure_ascii=False))
    st = [s.get("review", {}).get("status", "approved") for s in sb["shots"]]
    print(f"{st.count('approved')} fine, {st.count('change')} with notes")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", type=Path, help="scenes-approved.json saved from the page")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    if a.apply:
        apply(a.apply.expanduser())
        return
    sb = json.loads((HERE / "storyboard.json").read_text())
    out = HERE / "scenes.html"
    out.write_text(page(sb))
    print(f"Scenes: {out}")
    if not a.no_open and sys.platform == "darwin":
        subprocess.run(["open", str(out)])


if __name__ == "__main__":
    main()
