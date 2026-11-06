"""playcal visual: a screenshot gate. Save a PNG as the baseline, then check later shots against it
with a per-pixel tolerance, so antialiasing noise passes and a missing sprite fails. On a fail it
writes a diff PNG with the changed pixels in red. Standard library only (8- and 16-bit PNGs, no interlace).
"""
from __future__ import annotations

import argparse
import json
import shutil
import struct
import sys
import zlib
from pathlib import Path

from . import history
from .registry import Module

SIG = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}  # grey, RGB, grey+alpha, RGBA
DIM = bytes(v // 3 + 170 for v in range(256))  # unchanged pixels in the diff image: pale copy of the new shot


class PngError(ValueError):
    pass


def read_png(path: Path) -> tuple[int, int, list[bytes]]:
    """Returns (width, height, rows) with every row as RGBA bytes."""
    data = Path(path).read_bytes()
    if data[:8] != SIG:
        raise PngError(f"{path}: not a PNG")
    pos, idat, ihdr = 8, [], None
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + n
    if not ihdr:
        raise PngError(f"{path}: no IHDR")
    w, h, depth, ctype, _, _, interlace = ihdr
    if depth not in (8, 16) or ctype not in CHANNELS or interlace:
        raise PngError(f"{path}: only 8- or 16-bit grey/RGB/RGBA PNGs without interlace (got depth {depth}, type {ctype})")
    bpp = CHANNELS[ctype] * (depth // 8)
    raw = zlib.decompress(b"".join(idat))
    stride = w * bpp
    rows, prev = [], bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        cur = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if f == 1:
            for i in range(bpp, stride):
                cur[i] = (cur[i] + cur[i - bpp]) & 255
        elif f == 2:
            cur = bytearray((a + b) & 255 for a, b in zip(cur, prev))
        elif f == 3:
            for i in range(stride):
                cur[i] = (cur[i] + ((cur[i - bpp] if i >= bpp else 0) + prev[i]) // 2) & 255
        elif f == 4:
            for i in range(stride):
                a = cur[i - bpp] if i >= bpp else 0
                b, c = prev[i], (prev[i - bpp] if i >= bpp else 0)
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                cur[i] = (cur[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        elif f != 0:
            raise PngError(f"{path}: bad filter {f} on row {y}")
        prev = cur
        # 16-bit samples are compared at 8 bits: the high byte of each sample is kept.
        rows.append(_rgba(bytes(cur[0::2] if depth == 16 else cur), ctype, w))
    return w, h, rows


def _rgba(row: bytes, ctype: int, w: int) -> bytes:
    if ctype == 6:
        return row
    out = bytearray(w * 4)
    if ctype == 2:
        out[0::4], out[1::4], out[2::4], out[3::4] = row[0::3], row[1::3], row[2::3], b"\xff" * w
    elif ctype == 0:
        out[0::4] = out[1::4] = out[2::4] = row
        out[3::4] = b"\xff" * w
    else:
        out[0::4] = out[1::4] = out[2::4] = row[0::2]
        out[3::4] = row[1::2]
    return bytes(out)


def write_png(path: Path, w: int, h: int, rows: list[bytes]) -> None:
    chunk = lambda kind, body: struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    raw = b"".join(b"\x00" + r for r in rows)
    Path(path).write_bytes(SIG + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                          + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def diff(a: tuple[int, int, list[bytes]], b: tuple[int, int, list[bytes]], tolerance: int = 8,
         ignore: list[tuple[int, int, int, int]] = ()):
    """Pixels whose largest channel difference is above `tolerance` (0-255), skipping the `ignore`
    boxes (x0, y0, x1, y1, inclusive). Returns a summary and the diff image rows (changed pixels red,
    the rest the new shot dimmed)."""
    (wa, ha, ra), (wb, hb, rb) = a, b
    if (wa, ha) != (wb, hb):
        return {"same_size": False, "size": [wa, ha], "new_size": [wb, hb]}, None
    changed, box, out = 0, None, []
    for y in range(ha):
        x_row, y_row = ra[y], rb[y]
        if x_row == y_row:
            out.append(y_row.translate(DIM))
            continue
        row = bytearray(y_row.translate(DIM))
        skip = [(x0, x1) for x0, y0, x1, y1 in ignore if y0 <= y <= y1]
        for x in range(wa):
            if skip and any(x0 <= x <= x1 for x0, x1 in skip):
                continue
            i = x * 4
            d = max(abs(x_row[i] - y_row[i]), abs(x_row[i + 1] - y_row[i + 1]),
                    abs(x_row[i + 2] - y_row[i + 2]), abs(x_row[i + 3] - y_row[i + 3]))
            if d > tolerance:
                changed += 1
                row[i:i + 4] = b"\xff\x00\x00\xff"
                box = [x, y, x, y] if box is None else [min(box[0], x), min(box[1], y), max(box[2], x), y]
        out.append(bytes(row))
    total = wa * ha
    return {"same_size": True, "size": [wa, ha], "changed_pixels": changed,
            "changed_percent": round(100 * changed / total, 4), "box": box}, out


def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("action", choices=["save", "check"], help="save: make this PNG the baseline; check: compare a PNG to it")
    p.add_argument("name", help="baseline name, e.g. level4-start")
    p.add_argument("png", help="the screenshot (8- or 16-bit PNG)")
    p.add_argument("--dir", default=".playcal/visual", help="where baselines live (default .playcal/visual)")
    p.add_argument("--tolerance", type=int, default=8, help="per-channel difference a pixel may have, 0-255 (default 8)")
    p.add_argument("--max-changed", type=float, default=0.1, help="percent of pixels allowed to change (default 0.1)")
    p.add_argument("--ignore", action="append", default=[], metavar="X0,Y0,X1,Y1",
                   help="skip a box that changes on its own, like a clock or FPS counter (repeatable)")
    p.add_argument("--json", action="store_true", help="print the result as JSON")


def _run(args: argparse.Namespace) -> int:
    folder, png = Path(args.dir), Path(args.png)
    base = folder / f"{args.name}.png"
    try:
        new = read_png(png)
        if args.action == "save":
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(png, base)
            out, code = {"saved": str(base), "size": list(new[:2])}, 0
        elif not base.exists():
            out, code = {"error": f"no baseline {args.name} in {folder}; run save first"}, 2
        else:
            ignore = [tuple(int(v) for v in box.split(",")) for box in args.ignore]
            if any(len(box) != 4 for box in ignore):
                raise PngError("--ignore takes X0,Y0,X1,Y1")
            out, rows = diff(read_png(base), new, args.tolerance, ignore)
            ok = out["same_size"] and out["changed_percent"] <= args.max_changed
            out["verdict"] = "ok" if ok else "changed"
            if not ok and rows is not None:
                out["diff_png"] = str(folder / f"{args.name}.diff.png")
                write_png(Path(out["diff_png"]), *new[:2], rows)
            code = 0 if ok else 1
    except (OSError, PngError, zlib.error, ValueError) as e:
        out, code = {"error": str(e)}, 2
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    elif "error" in out:
        print(f"error: {out['error']}", file=sys.stderr)
    elif args.action == "save":
        print(f"saved {out['saved']} ({out['size'][0]}x{out['size'][1]})")
    elif not out["same_size"]:
        print(f"changed: size {out['size']} -> {out['new_size']}")
    else:
        print(f"{out['verdict']}: {out['changed_pixels']} pixels ({out['changed_percent']}%) differ"
              + (f", box {out['box']}, see {out['diff_png']}" if "diff_png" in out else ""))
    if args.action != "save":
        history.record("visual", args.action, out, code, args.name)
    return code


MODULE = Module(name="visual", summary="screenshot gate: compare a PNG to a saved baseline with a per-pixel tolerance, write a diff PNG on a fail",
                add_args=_add_args, run=_run)
