"""Command line. Every command has a Turkish and an English name."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

from .util import ROOT, has_filter, load_env, output_dir, set_ui_lang, t

COMMANDS = {  # canonical: aliases
    "setup": ["kurulum"], "doctor": ["doktor"], "new": ["yeni"], "run": ["uret", "devam"], "approve": ["onayla"],
    "revise": ["duzelt"], "status": ["durum"], "tick": [], "scheduler": ["zamanlayici"], "youtube-connect": ["youtube-baglan"],
    "puppet-example": ["kukla-ornek"], "references": ["referans"], "voices": ["sesler"],
}
ALIAS = {a: c for c, al in COMMANDS.items() for a in al + [c]}


def main(argv=None):
    load_env()
    ap = argparse.ArgumentParser(prog="studio.py", description="Auto Video Studio")
    ap.add_argument("command", help=" | ".join(f"{c} ({'/'.join(a)})" if a else c for c, a in COMMANDS.items()))
    ap.add_argument("args", nargs="*")
    ap.add_argument("--cevaplar", "--answers", dest="answers")
    ap.add_argument("--fikir", "--idea", dest="idea")
    ap.add_argument("--dosya", "--file", dest="file")
    ap.add_argument("--kur", "--install", dest="install", action="store_true")
    a = ap.parse_args(argv)
    cmd = ALIAS.get(a.command)
    if not cmd:
        ap.error(f"unknown command: {a.command}")
    if cmd == "setup":
        from . import wizard
        return wizard.run(a.answers)

    from . import config, pipeline
    cfg = config.load()
    set_ui_lang(cfg["ui_language"])

    if cmd == "doctor":
        return doctor(cfg)
    if cmd == "new":
        src = dict(mode="idea", idea=a.idea) if a.idea else dict(mode="manual", file=os.path.basename(a.file)) if a.file else None
        if a.file and not (pipeline.S.manual_dir() / os.path.basename(a.file)).exists():
            shutil.copy(a.file, pipeline.S.manual_dir())
        eid = pipeline.create(cfg, src)
        return pipeline.advance(cfg, eid)
    if cmd == "run":
        for eid in a.args or [e["id"] for e in pipeline.in_progress()]:
            pipeline.retry(cfg, eid)
        return
    if cmd == "approve":
        for eid in a.args:
            nxt = pipeline.approve(cfg, eid)
            print(t(f"{eid}: onaylandı → {nxt}", f"{eid}: approved → {nxt}"))
            pipeline.advance(cfg, eid)
        return
    if cmd == "revise":
        if len(a.args) < 2:
            raise SystemExit("studio.py duzelt <id> \"geri bildirim / feedback\"")
        pipeline.revise(cfg, a.args[0], " ".join(a.args[1:]))
        print(t(f"Yeni sürüm: {output_dir() / a.args[0] / 'senaryo.md'}", f"New version: {output_dir() / a.args[0] / 'senaryo.md'}"))
        return
    if cmd == "status":
        return status(cfg)
    if cmd == "tick":
        from . import scheduler
        return scheduler.tick(cfg)
    if cmd == "scheduler":
        from . import scheduler
        if a.install:
            desc, run = scheduler.install_command()
            print(desc)
            if run and input(t("Görev Zamanlayıcı'ya eklensin mi? (e/h) ", "Add to Task Scheduler? (y/n) ")).lower()[:1] in "ey":
                subprocess.run(run, check=False)
            elif not run:
                print(t("Eklemek için: crontab -e  (satırı yapıştırın)", "To add it: crontab -e  (paste the line)"))
            return
        return scheduler.loop(cfg)
    if cmd == "youtube-connect":
        from . import youtube
        print(t("Bağlanan kanal: ", "Connected channel: ") + youtube.connect())
        return
    if cmd == "puppet-example":
        from . import puppet
        for i, c in enumerate(cfg["characters"]):
            print(puppet.build_example_pack(c["id"], [(255, 152, 67), (90, 150, 255), (120, 200, 120), (230, 110, 160)][i % 4]))
        return
    if cmd == "references":
        from . import visuals
        for c in cfg["characters"]:
            if a.args and c["id"] not in a.args:
                continue
            print(visuals.make_reference(cfg, c["id"]))
        return
    if cmd == "voices":
        from .wizard import elevenlabs_voices
        for v in elevenlabs_voices():
            print(f"{v['id']}  {v['name']}  — {v['labels']}")


def status(cfg):
    from . import pipeline
    eps = pipeline.episodes()
    if not eps:
        print(t("Henüz bölüm yok.  python studio.py yeni", "No episodes yet.  python studio.py new"))
    for e in eps:
        up = ", ".join(u["url"] for u in (e.get("youtube") or {}).get("uploads", []))
        err = f"  ✗ {e['error'][:120]}" if e.get("error") else ""
        print(f"{e['id']:<55} {e['stage']:<14} {e.get('stats', {}).get('est_minutes', '')} {up}{err}")
    from .script import pending_ideas
    print(t(f"\nSıradaki fikir sayısı: {len(pending_ideas())}", f"\nIdeas in queue: {len(pending_ideas())}"))


def doctor(cfg):
    ok = True

    def row(good, msg, fix=""):
        nonlocal ok
        ok &= bool(good) or msg.startswith("(")
        print(("  ✓ " if good else "  ✗ ") + msg + ("" if good or not fix else f"\n      → {fix}"))

    print(t("Sistem", "System"))
    row(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]}", "Python 3.10+")
    row(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg / ffprobe", t("ffmpeg kurun (README > Gereksinimler)", "install ffmpeg (README)"))
    row(has_filter("rubberband"), t("(isteğe bağlı) ffmpeg rubberband: dil eşitlemede daha doğal esneme", "(optional) ffmpeg rubberband filter"),
        t("yoksa atempo kullanılır", "falls back to atempo"))
    if cfg["visuals"]["mode"] == "puppet":
        row(shutil.which("rhubarb"), t("(isteğe bağlı) Rhubarb Lip Sync: daha doğru dudak hareketi", "(optional) Rhubarb Lip Sync"),
            "https://github.com/DanielSWolf/rhubarb-lip-sync/releases")
    print(t("Anahtarlar", "Keys"))
    need = {}
    if cfg["llm"]["provider"] != "none":
        from .llm import KEY_NAMES
        need[KEY_NAMES[cfg["llm"]["provider"]]] = t("senaryo/SEO", "scripts/SEO")
    need.update({"elevenlabs": {"ELEVENLABS_API_KEY": t("seslendirme", "voices")}, "google": {"GOOGLE_TTS_API_KEY": t("seslendirme", "voices")},
                 "gemini": {"GEMINI_API_KEY": t("seslendirme", "voices")}}.get(cfg["voice"]["provider"], {}))
    if cfg["visuals"]["mode"] in ("ai_images", "puppet") and cfg["visuals"]["provider"] in ("gemini", "openai"):
        need[{"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}[cfg["visuals"]["provider"]]] = t("görseller", "images")
    for k, why in need.items():
        row(os.environ.get(k), f"{k} ({why})", t(".env dosyasına ekleyin", "add it to .env"))
    print(t("Kanal", "Channel"))
    if cfg["voice"]["provider"] != "placeholder":
        for sid in cfg.speaker_ids():
            from .tts import voice_of
            row(voice_of(cfg, sid)[0], t(f"ses kimliği: {sid}", f"voice id: {sid}"), "kanal/kanal.yaml → voice.id")
    if cfg["visuals"]["mode"] == "ai_images":
        from .visuals import reference
        for c in cfg["characters"]:
            row(reference(c["id"]), t(f"(önerilir) referans resmi: {c['id']}", f"(recommended) reference image: {c['id']}"),
                "python studio.py referans")
    if cfg["visuals"]["mode"] == "puppet":
        from .puppet import has_pack
        for c in cfg["characters"]:
            row(has_pack(c["id"]), t(f"kukla: {c['id']}", f"puppet: {c['id']}"), "python studio.py kukla-ornek")
    if cfg["youtube"]["enabled"]:
        from .youtube import client_secret, token_file
        print("YouTube")
        row(client_secret().exists(), t("OAuth istemci dosyası", "OAuth client file"), str(client_secret()))
        row(token_file().exists(), t("hesap bağlı", "account connected"), "python studio.py youtube-baglan")
    print(t("\nHer şey hazır." if ok else "\nYukarıdaki ✗ satırlarını düzeltin.", "\nAll good." if ok else "\nFix the ✗ lines above."))
    return ok
