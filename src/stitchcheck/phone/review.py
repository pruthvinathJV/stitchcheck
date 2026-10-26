"""Local review page for ingested phone photos: reject bad shots, fix labels.

python -m stitchcheck.phone.review [--dir data/phone] [--port 8765]   then open http://127.0.0.1:8765
python -m stitchcheck.phone.review --export [--out data/splits/phone_v1.csv]

Decisions are saved to <dir>/review.csv on every click. --export writes the final label file
(no images): accepted photos only, with the reviewed label.
"""
import argparse
import csv
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd

from stitchcheck.phone.ingest import TYPES

REJECT_REASONS = ["blurry", "not top-down", "seam not visible", "defect not visible", "duplicate", "other"]
REVIEW_FIELDS = ["photo_id", "status", "reason", "label"]


def load_reviews(path):
    if not path.exists():
        return {}
    with path.open() as f:
        return {r["photo_id"]: r for r in csv.DictReader(f)}


def save_reviews(path, reviews):
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_FIELDS)
        w.writeheader()
        w.writerows(reviews[k] for k in sorted(reviews))
    tmp.replace(path)


def final_labels(manifest, reviews):
    df = manifest.copy()
    df["status"] = [reviews.get(p, {}).get("status", "unreviewed") for p in df["photo_id"]]
    df["reason"] = [reviews.get(p, {}).get("reason", "") for p in df["photo_id"]]
    df["label_reviewed"] = [reviews.get(p, {}).get("label") or l for p, l in zip(df["photo_id"], df["label"])]
    return df


PAGE = """<!doctype html><meta charset="utf-8"><title>Phone set review</title>
<style>
body{font:14px system-ui;margin:16px;background:#fafafa;color:#222}
h2{margin:24px 0 8px;font-size:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.card{background:#fff;border:2px solid #ddd;border-radius:8px;padding:8px}
.card.accepted{border-color:#2e7d32}.card.rejected{border-color:#c62828;opacity:.55}
.card img{width:100%;aspect-ratio:1;object-fit:contain;background:#eee;border-radius:4px;cursor:zoom-in}
.row{display:flex;gap:6px;margin-top:6px;align-items:center;flex-wrap:wrap}
.meta{color:#666;font-size:12px}#summary{position:sticky;top:0;background:#fafafa;padding:8px 0;border-bottom:1px solid #ddd}
#zoom{position:fixed;inset:0;background:#000d;display:none;align-items:center;justify-content:center}
#zoom img{max-width:96vw;max-height:96vh}
</style>
<div id="summary"></div><div id="root"></div><div id="zoom" onclick="this.style.display='none'"><img></div>
<script>
const DATA = __DATA__, LABELS = __LABELS__, REASONS = __REASONS__;
function save(r){fetch('/api/review',{method:'POST',body:JSON.stringify(r)});render()}
function summary(){
  const c={accepted:0,rejected:0,unreviewed:0}; const byLabel={};
  for(const p of DATA){c[p.status]++; if(p.status==='accepted'){const l=p.label_reviewed;byLabel[l]=(byLabel[l]||0)+1}}
  document.getElementById('summary').textContent=`accepted ${c.accepted} · rejected ${c.rejected} · unreviewed ${c.unreviewed}  |  accepted by label: `+
    Object.entries(byLabel).map(([k,v])=>k+' '+v).join(', ');
}
function card(p){
  const d=document.createElement('div'); d.className='card '+p.status;
  d.innerHTML=`<img loading="lazy" src="/img/${p.photo_id}.jpg"><div class="meta">${p.photo_id} · ${p.fabric_desc} · ${p.source_file}</div>`;
  d.querySelector('img').onclick=()=>{const z=document.getElementById('zoom');z.querySelector('img').src=`/img/${p.photo_id}.jpg`;z.style.display='flex'};
  const row=document.createElement('div'); row.className='row';
  const ok=document.createElement('button'); ok.textContent='Accept';
  ok.onclick=()=>{p.status='accepted';p.reason='';save(p)};
  const rej=document.createElement('select');
  rej.innerHTML='<option value="">Reject…</option>'+REASONS.map(r=>`<option ${p.reason===r?'selected':''}>${r}</option>`).join('');
  rej.onchange=()=>{if(rej.value){p.status='rejected';p.reason=rej.value;save(p)}};
  const lab=document.createElement('select');
  lab.innerHTML=LABELS.map(l=>`<option ${p.label_reviewed===l?'selected':''}>${l}</option>`).join('');
  lab.onchange=()=>{p.label_reviewed=lab.value;save(p)};
  row.append(ok,rej,lab); d.append(row); return d;
}
function render(){
  summary(); const root=document.getElementById('root'); root.innerHTML='';
  const tags=[...new Set(DATA.map(p=>p.tag))];
  for(const t of tags){
    const ps=DATA.filter(p=>p.tag===t);
    const h=document.createElement('h2'); h.textContent=`${t} · ${ps[0].label} · ${ps.length} photos`+(ps[0].count_ok?'':' · COUNT MISMATCH'); root.append(h);
    const g=document.createElement('div'); g.className='grid'; ps.forEach(p=>g.append(card(p))); root.append(g);
  }
}
render();
</script>"""


def serve(phone_dir, port):
    manifest = pd.read_csv(phone_dir / "manifest.csv")
    review_path = phone_dir / "review.csv"
    reviews = load_reviews(review_path)
    images = (phone_dir / "images").resolve()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                df = final_labels(manifest, reviews)
                data = df.to_json(orient="records").replace("</", "<\\/")
                page = (PAGE.replace("__DATA__", data)
                        .replace("__LABELS__", json.dumps(list(TYPES.values())))
                        .replace("__REASONS__", json.dumps(REJECT_REASONS)))
                self._send(200, page.encode(), "text/html; charset=utf-8")
            elif self.path.startswith("/img/"):
                f = (images / Path(self.path[5:]).name).resolve()
                if f.parent == images and f.exists():
                    self._send(200, f.read_bytes(), "image/jpeg")
                else:
                    self._send(404, b"not found", "text/plain")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/api/review":
                return self._send(404, b"not found", "text/plain")
            r = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if r.get("photo_id") not in set(manifest["photo_id"]):
                return self._send(400, b"unknown photo", "text/plain")
            reviews[r["photo_id"]] = dict(photo_id=r["photo_id"], status=r["status"], reason=r.get("reason", ""),
                                          label=r.get("label_reviewed", ""))
            save_reviews(review_path, reviews)
            self._send(200, b"ok", "text/plain")

        def log_message(self, *args):
            pass

    print(f"reviewing {len(manifest)} photos at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def export(phone_dir, out):
    df = final_labels(pd.read_csv(phone_dir / "manifest.csv"), load_reviews(phone_dir / "review.csv"))
    pending = int((df["status"] == "unreviewed").sum())
    if pending:
        print(f"WARN {pending} photos are still unreviewed and are excluded")
    keep = df[df["status"] == "accepted"]
    cols = ["photo_id", "pair_id", "fabric", "fabric_desc", "chalk_mark", "label", "label_reviewed", "unseen", "md5"]
    out.parent.mkdir(parents=True, exist_ok=True)
    keep[cols].to_csv(out, index=False)
    print(f"{len(keep)} accepted photos -> {out}")
    print(pd.crosstab(keep["label_reviewed"], keep["fabric"], margins=True).to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=Path("data/phone"))
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--out", type=Path, default=Path("data/splits/phone_v1.csv"))
    args = ap.parse_args()
    if args.export:
        export(args.dir, args.out)
    else:
        serve(args.dir, args.port)


if __name__ == "__main__":
    main()
