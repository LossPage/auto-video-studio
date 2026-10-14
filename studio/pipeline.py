"""Episode pipeline and its saved state (cikti/<id>/durum.json).

stages:  script → [script_review] → visuals → render → seo → [upload_review] → upload → done
Bracketed stages are approval gates (kanal.yaml: script.approval, youtube.upload_approval); they wait for
`python studio.py onayla <id>`. During script review you may edit cikti/<id>/senaryo.md; the edits are used.
"""
from __future__ import annotations

import datetime as dt
import os
import traceback
from pathlib import Path

from . import llm, render, script as S, seo, tts, visuals
from .util import log, output_dir, read_json, t, write_json

ORDER = ["script", "script_review", "visuals", "render", "seo", "upload_review", "upload", "done"]
GATES = {"script_review", "upload_review"}


def ep_dir(eid: str) -> Path:
    return output_dir() / eid


def state(eid: str) -> dict:
    return read_json(ep_dir(eid) / "durum.json", {})


def save_state(eid: str, st: dict) -> None:
    st["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    write_json(ep_dir(eid) / "durum.json", st)


def episodes() -> list[dict]:
    out = []
    for p in sorted(output_dir().glob("*/durum.json")):
        s = read_json(p, {})
        if s:
            out.append(s)
    return out


def _cost(st: dict, key: str, data) -> None:
    st.setdefault("costs", {})[key] = data


# ---------------------------------------------------------------- new episode
def next_source(cfg) -> dict | None:
    mode = cfg["script"]["mode"]
    if mode == "manual":
        done = {e.get("source", {}).get("file") for e in episodes()}
        files = S.pending_manual(done)
        return dict(mode="manual", file=files[0].name) if files else None
    if mode == "idea":
        ideas = S.pending_ideas()
        if ideas:
            return dict(mode="idea", idea=ideas[0])
        if cfg["script"].get("fallback_to_ai") and llm.available(cfg):
            return dict(mode="ai")
        return None
    return dict(mode="ai")


def create(cfg, source: dict | None = None) -> str:
    source = source or next_source(cfg)
    if not source:
        where = S.manual_dir() if cfg["script"]["mode"] == "manual" else S.ideas_file()
        raise SystemExit(t(f"Sırada senaryo/fikir yok. Ekleyin: {where}", f"Nothing in the queue. Add one to: {where}"))
    if source["mode"] == "manual":
        parsed = S.parse_markdown((S.manual_dir() / source["file"]).read_text(encoding="utf-8"), cfg)
        sc = S.complete_manual(cfg, parsed)
    elif source["mode"] == "idea":
        sc = S.generate_ai(cfg, source["idea"])
    else:
        sc = S.generate_ai(cfg)
    return create_from_script(cfg, sc, source)


def create_from_script(cfg, sc: dict, source: dict, approved: bool = False) -> str:
    """Store a finished script as a new episode (used by `create` and by the setup wizard's test video)."""
    eid = S.episode_id(sc, cfg)
    n = 2
    while ep_dir(eid).exists():
        eid = f"{S.episode_id(sc, cfg)}-{n}"; n += 1
    sc["id"] = eid
    d = ep_dir(eid); d.mkdir(parents=True)
    write_json(d / "senaryo.json", sc)
    (d / "senaryo.md").write_text(S.to_markdown(sc, cfg), encoding="utf-8")
    if source["mode"] == "idea":
        S.mark_idea_used(source["idea"], eid)
    st = dict(id=eid, stage="script_review" if cfg["script"].get("approval", True) and not approved else "visuals", source=source,
              created=dt.datetime.now().isoformat(timespec="seconds"), stats=S.stats(sc, cfg))
    _cost(st, "llm_script", list(llm.USAGE)); llm.USAGE.clear()
    save_state(eid, st)
    log(t(f"Yeni bölüm: {eid}  ({st['stats']['est_minutes']} dk)", f"New episode: {eid}  ({st['stats']['est_minutes']} min)"))
    return eid


# ---------------------------------------------------------------- gates
def approve(cfg, eid: str) -> str:
    st = state(eid)
    if st.get("stage") == "script_review":
        _reload_markdown(cfg, eid)
        st["stage"] = "visuals"
    elif st.get("stage") == "upload_review":
        st["stage"] = "upload"
    else:
        raise SystemExit(t(f"{eid} onay beklemiyor (aşama: {st.get('stage')}).", f"{eid} is not waiting for approval (stage: {st.get('stage')})."))
    st.setdefault("approvals", []).append(dt.datetime.now().isoformat(timespec="seconds"))
    save_state(eid, st)
    return st["stage"]


def _reload_markdown(cfg, eid: str) -> None:
    """Use the owner's edits to senaryo.md made during review."""
    d = ep_dir(eid)
    md, js = d / "senaryo.md", d / "senaryo.json"
    if md.exists() and md.stat().st_mtime > js.stat().st_mtime + 1:
        old = read_json(js)
        c2 = type(cfg)(dict(cfg, script=dict(cfg["script"], written_language=cfg.primary)))
        new = S.parse_markdown(md.read_text(encoding="utf-8"), c2)
        new.pop("_missing_langs", None); new.pop("_written", None)
        for k in ("summary", "thumbnail_text", "topic_keywords", "shorts"):
            new[k] = new.get(k) or old.get(k)
        new = S.normalize(new, cfg)
        new["id"] = eid
        write_json(js, new)
        log(t("senaryo.md'deki düzenlemeler alındı.", "Edits from senaryo.md applied."))


def revise(cfg, eid: str, feedback: str) -> None:
    d = ep_dir(eid)
    st = state(eid)
    new = S.revise(cfg, read_json(d / "senaryo.json"), feedback)
    write_json(d / "senaryo.json", new)
    (d / "senaryo.md").write_text(S.to_markdown(new, cfg), encoding="utf-8")
    st["stage"] = "script_review" if cfg["script"].get("approval", True) else "visuals"
    st["stats"] = S.stats(new, cfg)
    st.setdefault("revisions", []).append(feedback)
    save_state(eid, st)


# ---------------------------------------------------------------- run stages
def advance(cfg, eid: str) -> str:
    """Run stages until a gate, an error or done. → final stage"""
    st = state(eid)
    d = ep_dir(eid)
    while st["stage"] not in GATES and st["stage"] != "done":
        stage = st["stage"]
        log(t(f"[{eid}] aşama: {stage}", f"[{eid}] stage: {stage}"))
        try:
            sc = read_json(d / "senaryo.json")
            if stage == "script":
                st["stage"] = "script_review" if cfg["script"].get("approval", True) else "visuals"
            elif stage == "visuals":
                _cost(st, "images", visuals.make_all(cfg, sc, d / "gorseller"))
                st["stage"] = "render"
            elif stage == "render":
                tts.USAGE.clear()
                info = render.render_episode(cfg, sc, d)
                write_json(d / "render.json", info)
                _cost(st, "voice", tts.cost_summary(cfg))
                st["stage"] = "seo"
            elif stage == "seo":
                info = read_json(d / "render.json")
                meta = seo.generate(cfg, sc, info)
                for lg in cfg.langs:
                    seo.thumbnail(cfg, sc, d, lg)
                write_json(d / "seo.json", meta)
                _write_publish_sheet(cfg, d, sc, info, meta)
                _cost(st, "llm_seo", list(llm.USAGE)); llm.USAGE.clear()
                st["stage"] = "upload_review" if (cfg["youtube"]["enabled"] and cfg["youtube"].get("upload_approval", True)) else "upload"
            elif stage == "upload":
                if cfg["youtube"]["enabled"]:
                    upload(cfg, eid, st)
                st["stage"] = "done"
            st.pop("error", None)
        except SystemExit:
            raise
        except Exception as e:
            st["error"] = f"{type(e).__name__}: {e}"
            st["trace"] = traceback.format_exc()[-3000:]
            save_state(eid, st)
            log(t(f"[{eid}] HATA ({stage}): {e}", f"[{eid}] ERROR ({stage}): {e}"))
            return stage
        save_state(eid, st)
    if st["stage"] in GATES:
        what = t("senaryo onayı", "script approval") if st["stage"] == "script_review" else t("yükleme onayı", "upload approval")
        log(t(f"[{eid}] {what} bekleniyor →  python studio.py onayla {eid}", f"[{eid}] waiting for {what} →  python studio.py approve {eid}"))
    return st["stage"]


def upload(cfg, eid: str, st: dict) -> None:
    from . import scheduler, youtube
    d = ep_dir(eid)
    meta = read_json(d / "seo.json")
    info = read_json(d / "render.json")
    yt = st.setdefault("youtube", {"uploads": []})
    done_files = {u["file"] for u in yt["uploads"]}
    publish_utc, publish_local = scheduler.publish_time(cfg) if cfg["youtube"]["privacy"] == "scheduled" else (None, None)
    multi = cfg["channel"]["multi_language"] == "multi_audio"
    caps = {lg: d / f for lg, f in info["subtitles"].items()} if cfg["youtube"].get("upload_captions", True) else {}
    videos = [(cfg.primary, info["videos"][cfg.primary])] if multi else list(info["videos"].items())
    for lg, f in videos:
        if f in done_files:
            continue
        vid = youtube.upload(cfg, d / f, meta["video"], lg, publish_utc, captions=caps if multi else {lg: caps.get(lg)} if caps else {},
                             thumbnail=d / f"kapak.{lg}.jpg" if cfg["youtube"].get("upload_thumbnail", True) else None,
                             other_langs=cfg.langs if multi else [])
        yt["uploads"].append(dict(file=f, lang=lg, id=vid, url=f"https://youtu.be/{vid}", publish_local=publish_local))
        save_state(eid, st)
    if cfg["youtube"].get("upload_shorts", True):
        for s in info.get("shorts", []):
            if s["file"] in done_files or not meta.get("shorts") or s["n"] > len(meta["shorts"]):
                continue
            m = meta["shorts"][s["n"] - 1]
            pu, pl = scheduler.publish_time(cfg) if cfg["youtube"]["privacy"] == "scheduled" else (None, None)
            vid = youtube.upload(cfg, d / s["file"], m, s["lang"], pu, other_langs=[])
            yt["uploads"].append(dict(file=s["file"], lang=s["lang"], id=vid, url=f"https://youtu.be/{vid}", publish_local=pl, short=True))
            save_state(eid, st)
    if multi and len(cfg.langs) > 1:
        extra = ", ".join(f"ses.{lg}.m4a" for lg in cfg.langs[1:])
        log(t(f"Hatırlatma: YouTube Studio > Diller > Ses ekle ile şu dosyaları ekleyin: {extra} (API bunu desteklemiyor).",
              f"Reminder: add {extra} in YouTube Studio > Languages > Add audio (not possible through the API)."))


def _write_publish_sheet(cfg, d: Path, sc: dict, info: dict, meta: dict) -> None:
    """yayin.md: everything needed to publish by hand or to review before upload."""
    rows = [f"# {sc['title'][cfg.primary]}", ""]
    for lg in cfg.langs:
        m = meta["video"][lg]
        rows += [f"## {lg.upper()}", "", f"**Title:** {m['title']}", "", "**Description:**", "", m["description"], "",
                 f"**Tags:** {', '.join(m['tags'])}", ""]
    for n, s in enumerate(meta.get("shorts", []), 1):
        rows += [f"## Short {n}", ""] + [f"- {lg}: {s[lg]['title']}" for lg in cfg.langs] + [""]
    if meta.get("warnings"):
        rows += ["## Warnings", ""] + [f"- {w}" for w in meta["warnings"]]
    (d / "yayin.md").write_text("\n".join(rows) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- orchestration
def in_progress() -> list[dict]:
    return [e for e in episodes() if e.get("stage") not in ("done",) and e.get("stage") not in GATES]


def produce_next(cfg) -> str | None:
    """Scheduled production: finish an unfinished episode first, otherwise start a new one."""
    busy = [e for e in in_progress() if not e.get("error")]
    eid = busy[0]["id"] if busy else create(cfg)
    advance(cfg, eid)
    return eid


def resume_all(cfg) -> list[str]:
    """Continue every episode that is past a gate (approved) and not failed."""
    out = []
    for e in in_progress():
        if e.get("error"):
            continue
        advance(cfg, e["id"]); out.append(e["id"])
    return out


def retry(cfg, eid: str) -> str:
    st = state(eid)
    st.pop("error", None); st.pop("trace", None)
    save_state(eid, st)
    return advance(cfg, eid)
