import struct
import zlib

from playcal import assets


def png(path, w, h):
    path.parent.mkdir(parents=True, exist_ok=True)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


def meta(path, texture_type=0, mip=1, readable=0, size=2048, comp=1, ios_size=None):
    extra = (f"  - serializedVersion: 3\n    buildTarget: iPhone\n    maxTextureSize: {ios_size}\n"
             "    textureCompression: 0\n") if ios_size else ""
    path.with_name(path.name + ".meta").write_text(
        "fileFormatVersion: 2\nTextureImporter:\n  mipmaps:\n    mipMapMode: 0\n"
        f"    enableMipMap: {mip}\n  isReadable: {readable}\n  textureType: {texture_type}\n"
        "  platformSettings:\n  - serializedVersion: 3\n    buildTarget: DefaultTexturePlatform\n"
        f"    maxTextureSize: {size}\n    textureCompression: {comp}\n" + extra, encoding="utf-8")


def test_ui_texture_with_mipmaps_and_wrong_type_fails(tmp_path):
    b = png(tmp_path / "Assets/UI/button.png", 64, 32)
    meta(b, texture_type=0, mip=1)
    out = assets.check(tmp_path, assets.DEFAULT_POLICY)
    msgs = {p["problem"]: p["message"] for p in out["problems"]}
    assert set(msgs) == {"texture_type", "mipmaps"}
    assert msgs["mipmaps"].startswith("mipmaps on, */UI/* wants mipmaps off")
    assert out["results"][0]["size"] == [64, 32]


def test_clean_project_and_the_catchall(tmp_path):
    meta(png(tmp_path / "Assets/UI/ok.png", 64, 64), texture_type=8, mip=0)
    meta(png(tmp_path / "Assets/Art/rock.png", 128, 128), ios_size=8192)  # an override doesn't count
    assert assets.check(tmp_path, assets.DEFAULT_POLICY)["ok"]
    meta(png(tmp_path / "Assets/Art/bad.png", 16, 16), readable=1, comp=0, size=8192)
    out = assets.check(tmp_path, assets.DEFAULT_POLICY)
    assert {p["problem"] for p in out["problems"] if p["file"].endswith("bad.png")} == {"readable", "compressed", "max_size"}


def test_oversized_image_without_a_cap(tmp_path):
    big = png(tmp_path / "ui/huge.png", 3000, 10)
    out = assets.check(tmp_path, {"rules": [{"match": "ui/*", "max_size": 2048}]})
    assert [p["problem"] for p in out["problems"]] == ["image_size"]
    big.with_name("huge.png.import").write_text("[params]\n\nprocess/size_limit=1024\nmipmaps/generate=false\n"
                                                "compress/mode=0\n", encoding="utf-8")
    assert assets.check(tmp_path, {"rules": [{"match": "ui/*", "max_size": 2048}]})["ok"]
    assert assets.import_settings(big) == {"engine": "godot", "mipmaps": False, "compressed": False, "max_size": 1024}


def test_plain_output_survives_a_console_that_cannot_encode_the_path(tmp_path):
    import os
    import subprocess
    import sys
    b = png(tmp_path / "Assets/UI/Kaçak.png", 8, 8)
    meta(b, texture_type=0, mip=1)
    env = {**os.environ, "PYTHONIOENCODING": "cp1254"}
    for extra in ([], ["--json"]):
        p = subprocess.run([sys.executable, "-m", "playcal.cli", "assets", "check", "--project-path", str(tmp_path), *extra],
                           capture_output=True, env=env)
        assert p.returncode == 1, p.stderr.decode("cp1254", "replace")
        assert b"Traceback" not in p.stderr


def test_tga_and_psd_image_size(tmp_path):
    tga = tmp_path / "ui" / "huge.tga"
    tga.parent.mkdir(parents=True, exist_ok=True)
    tga.write_bytes(b"\x00\x00\x02" + b"\x00" * 9 + struct.pack("<HH", 4096, 2048) + b"\x20\x00")
    psd = tmp_path / "ui" / "huge.psd"
    psd.write_bytes(b"8BPS\x00\x01" + b"\x00" * 6 + struct.pack(">HII", 4, 2160, 3840) + b"\x00\x08\x00\x03")
    out = assets.check(tmp_path, {"rules": [{"match": "ui/*", "max_size": 2048}]})
    by_file = {r["file"]: r["size"] for r in out["results"]}
    assert by_file == {"ui/huge.psd": [3840, 2160], "ui/huge.tga": [4096, 2048]}
    assert [(p["file"], p["problem"]) for p in out["problems"]] == [
        ("ui/huge.psd", "image_size"),
        ("ui/huge.tga", "image_size"),
    ]
