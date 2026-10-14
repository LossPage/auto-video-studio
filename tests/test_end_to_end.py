"""Full run with free placeholder voices and pictures: setup → episode → video with two audio tracks + Short."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_full_pipeline(tmp_path):
    env = dict(os.environ, STUDIO_ROOT=str(tmp_path), PYTHONPATH=str(REPO))
    run = lambda *a: subprocess.run([sys.executable, str(REPO / "studio.py"), *a], env=env, cwd=tmp_path,
                                    capture_output=True, text=True, timeout=900)
    r = run("kurulum", "--cevaplar", str(REPO / "ornekler" / "cevaplar-ornek.yaml"))
    assert r.returncode == 0, r.stdout + r.stderr
    # the wizard's last step renders the example script as the test video
    ep = next((tmp_path / "cikti").glob("*-ay-neden-bizi-takip-ediyor"))
    assert json.loads((ep / "durum.json").read_text())["stage"] == "upload_review"
    assert "Bir 'neden?' sorusu" in (tmp_path / "kanal/KANAL.md").read_text(encoding="utf-8")
    assert not (tmp_path / "kanal/.kurulum.json").exists()
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(ep / "video.mp4")],
                           capture_output=True, text=True).stdout.split()
    assert probe.count("audio") == 2 and probe.count("video") == 1
    assert (ep / "shorts" / "short-1.tr.mp4").exists()
    assert (ep / "altyazi.en.srt").read_text(encoding="utf-8").strip()
    seo_ = json.loads((ep / "seo.json").read_text())
    assert seo_["video"]["en"]["title"] == "Why Does the Moon Follow Us?"
    assert (ep / "kapak.tr.jpg").exists()
    # the test episode is not produced again; YouTube is off, so approving finishes it without an upload
    r = run("yeni")
    assert r.returncode != 0 and "kanal/icerik/senaryolar" in (r.stdout + r.stderr)
    r = run("onayla", ep.name)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((ep / "durum.json").read_text())["stage"] == "done"
