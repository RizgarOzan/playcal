"""playcal audio check: audio import settings against a folder policy, and WAV loudness, all offline.

Import settings come from the file next to each clip: Unity's `.meta` (AudioImporter: loadType,
forceToMono) or Godot's `.import` (force/mono; Godot has no load type for WAV). Rules match the
path relative to the project, first match wins. WAV files are also measured with the stdlib only:
integrated loudness (LUFS, ITU-R BS.1770 K-weighting and gating), true peak (4x oversampled, dBTP)
and clipped samples. A clip shorter than one 400 ms block is measured as one block.
"""
from __future__ import annotations

import argparse
import array
import fnmatch
import json
import math
import re
import struct
import sys
from pathlib import Path

from . import history
from .registry import Module

AUDIO = (".wav", ".ogg", ".mp3", ".aif", ".aiff", ".flac")
LOAD_TYPES = {0: "DecompressOnLoad", 1: "CompressedInMemory", 2: "Streaming"}
DEFAULT_POLICY = {"rules": [
    {"match": "*/UI/*", "load_type": ["DecompressOnLoad"], "mono": True, "max_true_peak": -1,
     "why": "short UI sounds should sit in memory; a streamed click starts late"},
    {"match": "*/SFX/*", "load_type": ["DecompressOnLoad", "CompressedInMemory"], "max_true_peak": -1,
     "why": "effects play often and short; streaming adds latency"},
    {"match": "*/Music/*", "load_type": ["Streaming"], "max_true_peak": -1,
     "why": "long music decompressed in memory costs megabytes"},
]}


# ---------- WAV ----------

def read_wav(path: Path) -> tuple[int, list[list[float]]]:
    """(sample rate, one list of floats in -1..1 per channel). PCM 8/16/24/32-bit and float32/64."""
    data = path.read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    pos, fmt, raw = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            tag, ch, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
            if tag == 0xFFFE and len(body) >= 26:
                tag = struct.unpack("<H", body[24:26])[0]
            fmt = (tag, ch, rate, bits)
        elif cid == b"data":
            raw = body
        pos += 8 + size + (size & 1)
    if not fmt or raw is None:
        raise ValueError("no fmt or data chunk")
    tag, ch, rate, bits = fmt
    if tag == 3 and bits in (32, 64):
        a = array.array("f" if bits == 32 else "d", raw[:len(raw) - len(raw) % (bits // 8)])
        vals = list(a)
    elif tag == 1 and bits == 8:
        vals = [(b - 128) / 128 for b in raw]
    elif tag == 1 and bits in (16, 32):
        a = array.array("h" if bits == 16 else "i", raw[:len(raw) - len(raw) % (bits // 8)])
        if sys.byteorder == "big":
            a.byteswap()
        full = float(1 << (bits - 1))
        vals = [v / full for v in a]
    elif tag == 1 and bits == 24:
        vals = [int.from_bytes(raw[i:i + 3], "little", signed=True) / 8388608.0 for i in range(0, len(raw) - 2, 3)]
    else:
        raise ValueError(f"unsupported WAV format (tag {tag}, {bits} bit)")
    return rate, [vals[c::ch] for c in range(ch)]


def _biquad(x: list[float], b: tuple, a: tuple) -> list[float]:
    b0, b1, b2 = b
    a1, a2 = a
    y, x1, x2, y1, y2 = [], 0.0, 0.0, 0.0, 0.0
    for v in x:
        o = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, v, y1, o
        y.append(o)
    return y


def _k_weight(x: list[float], rate: int) -> list[float]:
    # BS.1770 pre-filter for any sample rate (the libebur128 derivation)
    f0, g, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / rate)
    vh = 10 ** (g / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    x = _biquad(x, ((vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0),
                (2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0))
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / rate)
    a0 = 1 + k / q + k * k
    return _biquad(x, (1.0, -2.0, 1.0), (2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0))


def lufs(rate: int, chans: list[list[float]]) -> float | None:
    """Integrated loudness. None when the clip is digital silence."""
    w = [_k_weight(c, rate) for c in chans]
    n = len(w[0])
    size, step = int(0.4 * rate), int(0.1 * rate)
    starts = list(range(0, n - size + 1, step)) if n >= size else [0]
    size = min(size, n)
    if size == 0:
        return None
    blocks = []
    for s in starts:
        z = sum(sum(v * v for v in c[s:s + size]) / size for c in w)
        blocks.append(z)

    def loud(z: float) -> float:
        return -0.691 + 10 * math.log10(z) if z > 0 else -math.inf

    gated = [z for z in blocks if loud(z) > -70]
    if not gated:
        return None
    rel = loud(sum(gated) / len(gated)) - 10
    gated = [z for z in gated if loud(z) > rel]
    return round(loud(sum(gated) / len(gated)), 2)


_TAPS = 12  # per phase; a windowed sinc for 4x oversampling
_PHASES = [[(math.sin(math.pi * (k - _TAPS / 2 + 1 - p / 4)) / (math.pi * (k - _TAPS / 2 + 1 - p / 4))) *
            (0.5 + 0.5 * math.cos(math.pi * (k - _TAPS / 2 + 1 - p / 4) / (_TAPS / 2)))
            for k in range(_TAPS)] for p in (1, 2, 3)]


def true_peak(chans: list[list[float]]) -> tuple[float, int]:
    """(true peak in dBTP, clipped samples). Oversampled only near the loud parts, where the peak can be."""
    peak, clipped = 0.0, 0
    for c in chans:
        sp = max((abs(v) for v in c), default=0.0)
        clipped += sum(1 for v in c if abs(v) >= 0.9999)
        peak = max(peak, sp)
        for n in range(_TAPS // 2 - 1, len(c) - _TAPS // 2):
            if max(abs(c[n]), abs(c[n + 1])) < sp / 2:
                continue
            win = c[n - _TAPS // 2 + 1:n + _TAPS // 2 + 1]
            for taps in _PHASES:
                peak = max(peak, abs(sum(t * v for t, v in zip(taps, win))))
    return (round(20 * math.log10(peak), 2) if peak > 0 else -math.inf), clipped


def measure(path: Path) -> dict:
    rate, chans = read_wav(path)
    tp, clipped = true_peak(chans)
    return {"channels": len(chans), "rate": rate, "seconds": round(len(chans[0]) / rate, 3) if chans else 0,
            "lufs": lufs(rate, chans), "true_peak": tp, "clipped": clipped}


# ---------- import settings ----------

def import_settings(clip: Path) -> dict:
    meta, imp = Path(str(clip) + ".meta"), Path(str(clip) + ".import")
    if meta.exists():
        t = meta.read_text(encoding="utf-8", errors="replace")
        if "AudioImporter:" not in t:
            return {"engine": "unity"}
        lt = re.search(r"defaultSettings:\s*\n(?:\s+\w+:.*\n)*?\s+loadType:\s*(\d+)", t)
        mono = re.search(r"^\s*forceToMono:\s*(\d)", t, re.M)
        return {"engine": "unity", "load_type": LOAD_TYPES.get(int(lt[1]), lt[1]) if lt else None,
                "mono": bool(int(mono[1])) if mono else None}
    if imp.exists():
        mono = re.search(r"^force/mono\s*=\s*(\w+)", imp.read_text(encoding="utf-8", errors="replace"), re.M)
        return {"engine": "godot", "mono": (mono[1] == "true") if mono else None}
    return {}


def check_clip(clip: Path, rel: str, policy: dict) -> tuple[dict, list[dict]]:
    rule = next((r for r in policy.get("rules", []) if fnmatch.fnmatch("/" + rel, "*" + r["match"].lstrip("*"))
                 or fnmatch.fnmatch(rel, r["match"])), None)
    s = import_settings(clip)
    row: dict = {"file": rel, "settings": s, "rule": rule and rule["match"]}
    bad: list[dict] = []

    def fail(problem: str, msg: str) -> None:
        bad.append({"file": rel, "rule": rule and rule["match"], "problem": problem,
                    "message": msg + (f" ({rule['why']})" if rule and rule.get("why") and problem in ("load_type", "mono") else "")})

    m = None
    if clip.suffix.lower() == ".wav":
        try:
            m = row["measure"] = measure(clip)
        except (ValueError, OSError, struct.error) as e:
            fail("unreadable", f"could not read the WAV: {e}")
    if m and m["clipped"]:
        fail("clipping", f"{m['clipped']} samples at full scale, the clip is clipped")
    if rule:
        want = rule.get("load_type")
        if want and s.get("load_type") and s["load_type"] not in want:
            fail("load_type", f"load type {s['load_type']}, {rule['match']} wants {' or '.join(want)}")
        if rule.get("mono") and s.get("mono") is False and (m is None or m["channels"] > 1):
            fail("mono", f"stereo and Force To Mono is off, {rule['match']} wants mono")
        if m and m["true_peak"] is not None and "max_true_peak" in rule and m["true_peak"] > rule["max_true_peak"]:
            fail("true_peak", f"true peak {m['true_peak']} dBTP, over {rule['max_true_peak']} dBTP")
        lo, hi = (rule.get("lufs") or [None, None])
        if m and m["lufs"] is not None and ((lo is not None and m["lufs"] < lo) or (hi is not None and m["lufs"] > hi)):
            fail("lufs", f"{m['lufs']} LUFS, outside {lo}..{hi}")
    return row, bad


def check(project: Path, policy: dict, paths: list[str] | None = None) -> dict:
    files = [Path(p) for p in paths] if paths else sorted(
        f for f in project.rglob("*") if f.suffix.lower() in AUDIO and not any(
            part.startswith(".") or part in ("Library", "Temp") for part in f.relative_to(project).parts))
    rows, problems = [], []
    for f in files:
        try:
            rel = f.resolve().relative_to(project.resolve()).as_posix()
        except ValueError:
            rel = f.as_posix()
        row, bad = check_clip(f, rel, policy)
        rows.append(row)
        problems += bad
    return {"ok": not problems, "reason": "clean" if not problems else "policy_failed", "clips": len(rows),
            "problems": problems, "results": rows}


def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("action", choices=["check"], help="check: import settings against the policy, WAV loudness and peaks")
    p.add_argument("paths", nargs="*", help="clips to check (default: every audio file in the project)")
    p.add_argument("--project-path", default=".", help="Unity or Godot project folder (default .)")
    p.add_argument("--policy", metavar="FILE", help='JSON {"rules": [{"match": "*/UI/*", "load_type": [...], "mono": true, '
                   '"max_true_peak": -1, "lufs": [lo, hi]}]}; default: UI and SFX in memory, Music streamed, peaks under -1 dBTP')
    p.add_argument("--json", action="store_true", help="print the result as JSON")


def _run(args: argparse.Namespace) -> int:
    policy = json.loads(Path(args.policy).read_text(encoding="utf-8")) if args.policy else DEFAULT_POLICY
    out = check(Path(args.project_path), policy, args.paths)
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"{out['reason']}: {out['clips']} clips, {len(out['problems'])} problems")
        for b in out["problems"]:
            print(f"  {b['file']}: {b['message']}")
    code = 0 if out["ok"] else 1
    history.record("audio", "check", out, code, " ".join(args.paths))
    return code


MODULE = Module(name="audio", summary="check audio import settings (.meta / .import) against a folder policy, and WAV loudness (LUFS), true peak and clipping, offline",
                add_args=_add_args, run=_run, engines=("unity", "godot"))
