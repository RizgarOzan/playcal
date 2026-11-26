"""playcal assets check: texture import settings against a folder policy, offline.

Settings come from the file next to each image: Unity's `.meta` (TextureImporter: textureType,
enableMipMap, isReadable, the default platform's maxTextureSize and textureCompression) or Godot's
`.import` (mipmaps/generate, compress/mode, process/size_limit). The image's real size is read from
the PNG / JPEG header. Rules match the path relative to the project, first match wins, the same
shape as `playcal audio check`.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import struct
from pathlib import Path

from . import history
from .registry import Module

IMAGES = (".png", ".jpg", ".jpeg", ".tga", ".psd", ".tif", ".tiff", ".bmp", ".exr", ".hdr")
TEXTURE_TYPES = {0: "Default", 1: "NormalMap", 2: "Editor GUI", 4: "Cookie", 6: "Lightmap", 7: "Cursor",
                 8: "Sprite", 10: "SingleChannel", 11: "Shadowmask", 12: "DirectionalLightmap"}
COMPRESSION = {0: "None", 1: "Normal", 2: "HighQuality", 3: "LowQuality"}
DEFAULT_POLICY = {"rules": [
    {"match": "*/UI/*", "texture_type": ["Sprite"], "mipmaps": False, "max_size": 2048,
     "why": "UI draws at screen size; mipmaps add a third more memory and blur it"},
    {"match": "*", "readable": False, "compressed": True, "max_size": 4096,
     "why": "Read/Write keeps a second copy in RAM and uncompressed textures cost 4-8x the memory"},
]}


def image_size(path: Path) -> tuple[int, int] | None:
    with path.open("rb") as f:
        head = f.read(32)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])
        if head[:4] == b"8BPS" and len(head) >= 22:
            h, w = struct.unpack(">II", head[14:22])
            return w, h
        if path.suffix.lower() == ".tga" and len(head) >= 18:
            return struct.unpack("<HH", head[12:16])
        if head[:2] == b"\xff\xd8":
            f.seek(2)
            while True:
                m = f.read(4)
                if len(m) < 4 or m[0] != 0xFF:
                    return None
                size = struct.unpack(">H", m[2:4])[0]
                if m[1] in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", f.read(5)[1:5])
                    return w, h
                f.seek(size - 2, 1)
    return None


def import_settings(img: Path) -> dict:
    meta, imp = Path(str(img) + ".meta"), Path(str(img) + ".import")
    if meta.exists():
        t = meta.read_text(encoding="utf-8", errors="replace")
        if "TextureImporter:" not in t:
            return {"engine": "unity"}

        def num(pattern: str, text: str = t) -> int | None:
            m = re.search(pattern, text, re.M)
            return int(m[1]) if m else None

        # the default platform block, not a per-platform override
        plat = re.search(r"buildTarget:\s*DefaultTexturePlatform\s*\n((?:\s+\w+:.*\n)+)", t)
        block = plat[1] if plat else t
        tt, mip, rw = num(r"^\s*textureType:\s*(-?\d+)"), num(r"^\s*enableMipMap:\s*(\d)"), num(r"^\s*isReadable:\s*(\d)")
        size, comp = num(r"^\s*maxTextureSize:\s*(\d+)", block), num(r"^\s*textureCompression:\s*(\d)", block)
        return {"engine": "unity", "texture_type": TEXTURE_TYPES.get(tt, tt) if tt is not None else None,
                "mipmaps": bool(mip) if mip is not None else None, "readable": bool(rw) if rw is not None else None,
                "max_size": size, "compressed": (comp != 0) if comp is not None else None,
                "compression": COMPRESSION.get(comp, comp) if comp is not None else None}
    if imp.exists():
        t = imp.read_text(encoding="utf-8", errors="replace")

        def val(key: str) -> str | None:
            m = re.search(rf"^{re.escape(key)}\s*=\s*(\S+)", t, re.M)
            return m[1] if m else None

        mip, mode, lim = val("mipmaps/generate"), val("compress/mode"), val("process/size_limit")
        return {"engine": "godot", "mipmaps": (mip == "true") if mip else None,
                # compress/mode 0 Lossless, 1 Lossy, 2 VRAM Compressed, 3 VRAM Uncompressed, 4 Basis Universal
                "compressed": (mode in ("2", "4")) if mode else None,
                "max_size": (int(lim) or None) if lim and lim.isdigit() else None}
    return {}


def check_image(img: Path, rel: str, policy: dict) -> tuple[dict, list[dict]]:
    rule = next((r for r in policy.get("rules", []) if fnmatch.fnmatch("/" + rel, "*" + r["match"].lstrip("*"))
                 or fnmatch.fnmatch(rel, r["match"])), None)
    s = import_settings(img)
    try:
        size = image_size(img)
    except OSError:
        size = None
    row = {"file": rel, "settings": s, "size": list(size) if size else None, "rule": rule and rule["match"]}
    bad: list[dict] = []
    if not rule:
        return row, bad

    def fail(problem: str, msg: str) -> None:
        bad.append({"file": rel, "rule": rule["match"], "problem": problem,
                    "message": msg + (f" ({rule['why']})" if rule.get("why") else "")})

    want = rule.get("texture_type")
    if want and s.get("texture_type") and s["texture_type"] not in want:
        fail("texture_type", f"texture type {s['texture_type']}, {rule['match']} wants {' or '.join(want)}")
    for key, on, off in (("mipmaps", "mipmaps on", "mipmaps off"), ("readable", "Read/Write on", "Read/Write off"),
                         ("compressed", "compressed", "uncompressed")):
        if key in rule and s.get(key) is not None and s[key] != rule[key]:
            fail(key, f"{on if s[key] else off}, {rule['match']} wants {on if rule[key] else off}")
    cap = rule.get("max_size")
    if cap and s.get("max_size") and s["max_size"] > cap:
        fail("max_size", f"max size {s['max_size']}, over {cap}")
    if cap and size and max(size) > cap and not (s.get("max_size") and s["max_size"] <= cap):
        fail("image_size", f"image is {size[0]}x{size[1]} and nothing caps it at {cap}")
    return row, bad


def check(project: Path, policy: dict, paths: list[str] | None = None) -> dict:
    files = [Path(p) for p in paths] if paths else sorted(
        f for f in project.rglob("*") if f.suffix.lower() in IMAGES and not any(
            part.startswith(".") or part in ("Library", "Temp") for part in f.relative_to(project).parts))
    rows, problems = [], []
    for f in files:
        try:
            rel = f.resolve().relative_to(project.resolve()).as_posix()
        except ValueError:
            rel = f.as_posix()
        row, bad = check_image(f, rel, policy)
        rows.append(row)
        problems += bad
    return {"ok": not problems, "reason": "clean" if not problems else "policy_failed", "textures": len(rows),
            "problems": problems, "results": rows}


def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("action", choices=["check"], help="check: texture import settings against the policy")
    p.add_argument("paths", nargs="*", help="images to check (default: every image in the project)")
    p.add_argument("--project-path", default=".", help="Unity or Godot project folder (default .)")
    p.add_argument("--policy", metavar="FILE", help='JSON {"rules": [{"match": "*/UI/*", "texture_type": ["Sprite"], '
                   '"mipmaps": false, "readable": false, "compressed": true, "max_size": 2048}]}; default: UI as '
                   'sprites without mipmaps, everything compressed, not readable, at most 4096')
    p.add_argument("--json", action="store_true", help="print the result as JSON")


def _run(args: argparse.Namespace) -> int:
    policy = json.loads(Path(args.policy).read_text(encoding="utf-8")) if args.policy else DEFAULT_POLICY
    out = check(Path(args.project_path), policy, args.paths)
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"{out['reason']}: {out['textures']} textures, {len(out['problems'])} problems")
        for b in out["problems"]:
            print(f"  {b['file']}: {b['message']}")
    code = 0 if out["ok"] else 1
    history.record("assets", "check", out, code, " ".join(args.paths))
    return code


MODULE = Module(name="assets", summary="check texture import settings (.meta / .import) against a folder policy: type, mipmaps, Read/Write, compression, max size, offline",
                add_args=_add_args, run=_run, engines=("unity", "godot"))
