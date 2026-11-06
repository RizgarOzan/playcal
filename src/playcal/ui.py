"""playcal ui: draw the run history (.playcal/history.jsonl) as one static HTML page, newest run first.
One row per loop, observe, replay or visual run: what ran, the verdict, the errors with file:line,
failed checks, the scene diff, the checkpoint to undo to, and the screenshot or visual diff image.
No server and no scripts: open the file in any browser."""
from __future__ import annotations

import argparse
import html
import os
import sys
import time
import webbrowser
from pathlib import Path

from . import history
from .registry import Module

CSS = """
:root{--bg:#fbfbfa;--fg:#1d1d1f;--mute:#6b6b70;--line:#e4e4e2;--ok:#1f7a3a;--bad:#b3261e;--chip:#f0f0ee}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#ececee;--mute:#9a9aa2;--line:#2c2c30;--ok:#5cc77e;--bad:#ff7b72;--chip:#232327}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}h1{font-size:18px;margin:0 0 4px}p.sub{color:var(--mute);margin:0 0 20px}
.run{display:grid;grid-template-columns:9rem 1fr auto;gap:4px 16px;padding:12px 0;border-top:1px solid var(--line)}
.when{color:var(--mute);font-variant-numeric:tabular-nums}.what code{background:var(--chip);padding:1px 5px;border-radius:4px}
.v{font-weight:600}.v.ok{color:var(--ok)}.v.bad{color:var(--bad)}ul{margin:4px 0 0;padding-left:18px}li{overflow-wrap:anywhere}
.mute{color:var(--mute)}img{max-width:220px;max-height:140px;border:1px solid var(--line);border-radius:4px;display:block}
@media (max-width:640px){.run{grid-template-columns:1fr}}
"""


def _lines(e: dict) -> list[str]:
    """Plain-text detail lines for one run, whatever tool wrote it."""
    out, rows = e.get("out") or {}, []
    for err in (out.get("compile") or {}).get("errors", []) + (out.get("console") or {}).get("errors", []):
        at = f"{err['file']}:{err.get('line')} " if err.get("file") else ""
        rows.append(f"{at}{err.get('code', '')}: {err.get('message', '')}".strip())
    for err in (out.get("result") or {}).get("errors", []) + out.get("read_errors", []) + out.get("play_errors", []):
        rows.append(f"{err.get('code', '')}: {err.get('message', '')}")
    rows += [f"FAIL {c['expect']} (got {c.get('actual')!r})" for c in out.get("checks", []) if not c.get("ok")]
    df = out.get("diff")
    if isinstance(df, dict) and not df.get("same", True):
        rows.append(f"scene: {df.get('added', 0)} added, {df.get('removed', 0)} removed, {df.get('changed', 0)} changed")
    elif isinstance(df, list) and df:
        rows.append(f"{len(df)} values changed")
    div = out.get("first_divergence")
    if div:
        rows.append(f"diverged at frame {div.get('frame')}")
    if "changed_percent" in out:
        rows.append(f"{out['changed_percent']}% of pixels differ")
    for k in ("fault", "problem", "error", "hint"):
        if out.get(k):
            rows.append(str(out[k]))
    if out.get("undo"):
        rows.append(f"undo: {out['undo']}")
    return rows


def _verdict(e: dict) -> str:
    out = e.get("out") or {}
    return str(out.get("reason") or out.get("verdict") or ("error" if out.get("error") else "done"))


def _image(e: dict, page_dir: Path) -> str | None:
    out = e.get("out") or {}
    src = out.get("diff_png") or out.get("shot")
    if not src or not Path(src).exists():
        return None
    try:
        return os.path.relpath(Path(src).resolve(), page_dir.resolve()).replace(os.sep, "/")
    except ValueError:  # another drive on Windows
        return Path(src).resolve().as_uri()


def render(entries: list[dict], page_dir: Path) -> str:
    esc = html.escape
    rows = []
    for e in sorted(entries, key=lambda x: x.get("t", 0), reverse=True):
        ok = e.get("exit") == 0
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.get("t", 0)))
        cmd = f" <code>{esc(e['cmd'])}</code>" if e.get("cmd") else ""
        cp = (e.get("out") or {}).get("checkpoint") or {}
        cp_txt = f' <span class="mute">checkpoint {esc(cp["id"])}</span>' if cp.get("id") else ""
        detail = "".join(f"<li>{esc(line)}</li>" for line in _lines(e))
        img = _image(e, page_dir)
        pic = f'<a href="{esc(img)}"><img src="{esc(img)}" alt="screenshot"></a>' if img else ""
        rows.append(
            f'<div class="run"><div class="when">{esc(when)}</div>'
            f'<div class="what"><b>{esc(e.get("tool", "?"))} {esc(e.get("action", ""))}</b>{cmd}{cp_txt}<br>'
            f'<span class="v {"ok" if ok else "bad"}">{esc(_verdict(e))}</span>'
            f'{f"<ul>{detail}</ul>" if detail else ""}</div>'
            f'<div>{pic}</div></div>')
    red = sum(1 for e in entries if e.get("exit") != 0)
    body = "".join(rows) or '<p class="mute">No runs yet. Run playcal loop, observe, replay or visual check first.</p>'
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>playcal runs</title><style>{CSS}</style></head>'
            f'<body><main><h1>playcal runs</h1><p class="sub">{len(entries)} runs, {red} red. Newest first.</p>'
            f'{body}</main></body></html>')


def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--history", help=f"history file (default: $PLAYCAL_HISTORY or {history.DEFAULT})")
    p.add_argument("--out", default=".playcal/ui.html", help="where to write the page (default .playcal/ui.html)")
    p.add_argument("--last", type=int, metavar="N", help="only the newest N runs")
    p.add_argument("--open", action="store_true", help="open the page in the default browser")


def _run(args: argparse.Namespace) -> int:
    src = Path(args.history) if args.history else history.path()
    if src is None or not src.exists():
        print(f"playcal ui: no history at {src}; run loop, observe, replay or visual check first", file=sys.stderr)
        return 2
    entries = sorted(history.read(src), key=lambda x: x.get("t", 0))
    if args.last:
        entries = entries[-args.last:]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(entries, out.parent), encoding="utf-8")
    print(f"wrote {out} ({len(entries)} runs)")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


MODULE = Module(name="ui", summary="draw the run history (loop, observe, replay, visual) as one static HTML page, newest first",
                add_args=_add_args, run=_run)
