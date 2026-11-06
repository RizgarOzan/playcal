import math
import struct

import pytest

from playcal import audio

RATE = 48000


def wav(path, chans, bits=16, rate=RATE):
    path.parent.mkdir(parents=True, exist_ok=True)
    n, ch = len(chans[0]), len(chans)
    full = (1 << (bits - 1)) - 1
    frames = b"".join(
        b"".join(int(round(max(-1.0, min(1.0, c[i])) * full)).to_bytes(bits // 8, "little", signed=True) for c in chans)
        for i in range(n))
    fmt = struct.pack("<HHIIHH", 1, ch, rate, rate * ch * bits // 8, ch * bits // 8, bits)
    path.write_bytes(b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
                     + b"data" + struct.pack("<I", len(frames)) + frames)
    return path


def sine(db, seconds=1.0, f=997):
    a = 10 ** (db / 20)
    return [a * math.sin(2 * math.pi * f * i / RATE) for i in range(int(seconds * RATE))]


def meta(path, load_type, mono):
    path.with_name(path.name + ".meta").write_text(
        "fileFormatVersion: 2\nAudioImporter:\n  serializedVersion: 7\n  defaultSettings:\n    serializedVersion: 2\n"
        f"    loadType: {load_type}\n    sampleRateSetting: 0\n  forceToMono: {mono}\n  loadInBackground: 0\n",
        encoding="utf-8")


# six fixtures with known loudness: a 997 Hz sine at L dBFS is L - 3.01 LUFS mono, L LUFS on two channels
@pytest.mark.parametrize("db,chans,bits,want", [
    (-20, 1, 16, -23.01), (-10, 1, 16, -13.01), (-30, 1, 24, -33.01),
    (-20, 2, 16, -20.0), (-26, 2, 24, -26.0), (-6, 1, 16, -9.01)])
def test_lufs_of_known_sines(tmp_path, db, chans, bits, want):
    s = sine(db)
    m = audio.measure(wav(tmp_path / "t.wav", [s] * chans, bits))
    assert m["lufs"] == pytest.approx(want, abs=0.1)
    assert m["true_peak"] == pytest.approx(db, abs=0.2) and m["clipped"] == 0


def test_true_peak_finds_intersample_overs(tmp_path):
    # fs/4 sine at 45 degrees: every sample sits at 0.707 of the real peak
    s = [math.sin(math.pi / 2 * i + math.pi / 4) * 0.99 for i in range(RATE // 2)]
    m = audio.measure(wav(tmp_path / "isp.wav", [s]))
    assert m["true_peak"] > 20 * math.log10(0.99 * 0.7072) + 2.5


def test_stereo_ui_click_on_streaming_fails(tmp_path):
    click = wav(tmp_path / "Assets/UI/click.wav", [sine(-12, 0.1)] * 2)
    meta(click, 2, 0)
    out = audio.check(tmp_path, audio.DEFAULT_POLICY)
    assert not out["ok"] and out["reason"] == "policy_failed"
    probs = {b["problem"]: b["message"] for b in out["problems"]}
    assert probs["load_type"].startswith("load type Streaming, */UI/* wants DecompressOnLoad")
    assert "stereo and Force To Mono is off" in probs["mono"]


def test_good_project_is_clean_and_clipping_is_caught(tmp_path):
    ok = wav(tmp_path / "Assets/UI/ok.wav", [sine(-12, 0.1)])
    meta(ok, 0, 1)
    music = wav(tmp_path / "Assets/Music/theme.wav", [sine(-18)] * 2)
    meta(music, 2, 0)
    assert audio.check(tmp_path, audio.DEFAULT_POLICY)["ok"]
    hot = wav(tmp_path / "Assets/SFX/hit.wav", [[1.0 if i % 2 else -1.0 for i in range(4800)]])
    meta(hot, 0, 0)
    out = audio.check(tmp_path, audio.DEFAULT_POLICY)
    assert {b["problem"] for b in out["problems"]} >= {"clipping", "true_peak"}


def test_godot_import_mono(tmp_path):
    c = wav(tmp_path / "ui/click.wav", [sine(-12, 0.1)] * 2)
    c.with_name("click.wav.import").write_text('[params]\n\nforce/mono=false\n', encoding="utf-8")
    out = audio.check(tmp_path, {"rules": [{"match": "ui/*", "mono": True}]})
    assert [b["problem"] for b in out["problems"]] == ["mono"]
    assert audio.import_settings(c) == {"engine": "godot", "mono": False}
