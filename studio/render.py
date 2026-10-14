"""Timeline, frames and sound → finished videos (long episode, Shorts), audio tracks and subtitles.

Picture: shot image with a slow camera move (Ken Burns), optional puppets with lip sync, optional burned subtitles,
fades at scene changes. Sound: voice clips on a shared timeline, optional music bed with ducking, loudness
normalized for YouTube (-14 LUFS). Multi-language: one picture with one audio track per language
(multi_audio) or one video per language (separate_videos).
"""
from __future__ import annotations

import importlib.util
import math
import os
import random
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import puppet, tts
from .util import (SR, cache_dir, ffmpeg, fmt_ts, h, lang_iso3, lang_name, load_audio, log, need, save_wav, t)
from .visuals import cover, font, image_for

FPS = 24
TEMPO = {"normal": dict(lead=.45, gap=.35, tail=.45, fade=.35, intro=3.0),
         "sleep": dict(lead=1.0, gap=.9, tail=1.2, fade=1.2, intro=4.0)}
SHORT_MAX = 58.0


def tempo(cfg):
    return TEMPO["sleep" if cfg["channel"]["type"] == "sleep" else "normal"]


# ---------------------------------------------------------------- timeline
def voice_all(cfg, script, langs) -> dict:
    jobs = [(sh["id"], i, l) for sc in script["scenes"] for sh in sc["shots"] for i, l in enumerate(sh["lines"])]
    log(t(f"seslendirme: {len(jobs)} replik × {len(langs)} dil", f"voicing: {len(jobs)} lines × {len(langs)} languages"))
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda j: tts.voice_line(cfg, j[2], langs), jobs))
    return {(j[0], j[1]): r for j, r in zip(jobs, res)}


def timeline(cfg, script, langs, shot_ids=None, intro=True) -> dict:
    T = tempo(cfg)
    voiced = voice_all(cfg, script, langs)
    items, t0, prev_scene = [], 0.0, None
    if intro and cfg["format"].get("intro_card", True):
        items.append(dict(kind="intro", start=0.0, dur=T["intro"], lines=[], new_scene=True, scene=script["scenes"][0],
                          shot=script["scenes"][0]["shots"][0]))
        t0 = T["intro"]
    chapters = []
    for sc in script["scenes"]:
        for sh in sc["shots"]:
            if shot_ids is not None and sh["id"] not in shot_ids:
                continue
            new = sc["id"] != prev_scene
            if new:
                chapters.append((round(t0, 2), sc["title"]))
            prev_scene = sc["id"]
            x, lines = T["lead"], []
            for i, l in enumerate(sh["lines"]):
                v = voiced[(sh["id"], i)]
                lines.append(dict(start=x, slot=v["slot"], clips=v["clips"], lead=v["lead"], drift=v["drift"],
                                  speaker=l["speaker"], text=l["text"]))
                x += v["slot"] + T["gap"] * (1.6 if l["text"][langs[0]].rstrip().endswith("?") else 1)
            dur = max(2.5, x - T["gap"] + T["tail"] + sh.get("pause_after", 0)) if lines else 3.0
            items.append(dict(kind="shot", start=t0, dur=round(dur, 3), lines=lines, new_scene=new, scene=sc, shot=sh))
            t0 += dur
    for a, b in zip(items, items[1:]):
        a["next_new_scene"] = b["new_scene"]
    if items:
        items[-1]["next_new_scene"] = True
    drift = [l["drift"] for it in items for l in it["lines"]]
    return dict(items=items, total=round(t0, 3), chapters=chapters, langs=langs,
                drift=dict(over_05s=sum(d > .5 for d in drift), max=max(drift, default=0)))


def trim_short(tl: dict, limit=SHORT_MAX) -> dict:
    keep, t0 = [], 0.0
    for it in tl["items"]:
        if keep and t0 + it["dur"] > limit:
            break
        keep.append(dict(it, start=t0)); t0 += it["dur"]
    keep[-1]["next_new_scene"] = True
    return dict(tl, items=keep, total=round(t0, 3))


# ---------------------------------------------------------------- camera
def window(cam: str, u: float, src, out_aspect: float, focus_x: float = .5):
    """→ (x0, y0, w, h) crop of the source image at progress u (0..1)."""
    SW, SH = src
    e = u * u * (3 - 2 * u)
    z = {"static": (1.0, 1.04), "zoom_in": (1.0, 1.13), "zoom_out": (1.13, 1.0), "pan_left": (1.1, 1.1), "pan_right": (1.1, 1.1)}[cam]
    z = z[0] + (z[1] - z[0]) * e
    if out_aspect >= SW / SH:
        w = SW / z; hgt = w / out_aspect
    else:
        hgt = SH / z; w = hgt * out_aspect
    if cam in ("pan_left", "pan_right"):
        span = (SW - w) * (.7 if out_aspect < 1 < SW / SH else .5)
        cx = SW / 2 + (e - .5) * span * (1 if cam == "pan_right" else -1)
    else:
        cx = SW * focus_x if out_aspect < SW / SH else SW / 2 + (focus_x - .5) * SW * .3 * e
    cy = SH / 2 + (SH * .03 * e if cam == "zoom_in" else 0)
    x0 = min(max(cx - w / 2, 0), SW - w)
    y0 = min(max(cy - hgt / 2, 0), SH - hgt)
    return x0, y0, w, hgt


# ---------------------------------------------------------------- subtitles
def wrap(text: str, n: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        if len(cur) + len(w) + 1 > n and cur:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    return lines + ([cur] if cur else [])


def chunks(text: str, n: int, maxl: int = 2) -> list[str]:
    sents = [s.strip() for s in re.findall(r"[^.!?…]+[.!?…]*", text) if s.strip()] or [text]
    out, cur = [], []
    for s in sents:
        if cur and len(wrap(" ".join(cur + [s]), n)) > maxl:
            out.append(" ".join(cur)); cur = [s]
        else:
            cur.append(s)
    if cur:
        out.append(" ".join(cur))
    res = []
    for c in out:
        ls = wrap(c, n)
        res += [" ".join(ls[i:i + maxl]) for i in range(0, len(ls), maxl)]
    return res


def sub_cues(lines, lang, n=42):
    cues = []
    for l in lines:
        cs = chunks(l["text"].get(lang, ""), n)
        tot = sum(len(c) for c in cs) or 1
        x = l["start"]
        for c in cs:
            span = l["slot"] * len(c) / tot
            cues.append((x, x + span, c)); x += span
    return cues


def sub_image(text: str, W: int, H: int, vertical: bool):
    fs = 58 if vertical else 44
    f = font(fs)
    ls = wrap(text, 22 if vertical else 42)
    lh = int(fs * 1.25)
    widths = [f.getlength(x) for x in ls]
    bw, bh = int(max(widths) + 60), lh * len(ls) + 30
    im = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, bw - 1, bh - 1), 22, fill=(20, 16, 36, 150))
    for i, x in enumerate(ls):
        d.text(((bw - widths[i]) / 2, 15 + i * lh), x, font=f, fill=(255, 250, 235), stroke_width=3, stroke_fill=(20, 16, 36))
    y = int(H * .14) if vertical else H - bh - 30          # Shorts: above the characters, below the top UI
    return im, ((W - bw) // 2, y)


# ---------------------------------------------------------------- frames (one worker per shot)
def intro_frame(img: Path, title: str, channel: str, size) -> Image.Image:
    W, H = size
    bg = cover(Image.open(img), size).filter(ImageFilter.GaussianBlur(18))
    bg = Image.blend(bg, Image.new("RGB", size, (20, 16, 36)), .35)
    d = ImageDraw.Draw(bg)
    tw = 20 if H > W else 32
    for i, ln in enumerate(wrap(title, tw)[:3]):
        d.text((W / 2, H * .4 + i * (H * .085)), ln, font=font(int(H * (.055 if H > W else .085))), fill=(255, 248, 230),
               anchor="mm", stroke_width=6, stroke_fill=(30, 22, 48))
    d.text((W / 2, H * .78), channel, font=font(int(H * .04)), fill=(255, 220, 150), anchor="mm", stroke_width=4, stroke_fill=(30, 22, 48))
    return bg


def render_item(args):
    it, src_img, size, seg, sub_lang, cfg_bits = args
    W, H = size
    fade_d = cfg_bits["fade"]
    nfr = max(1, int(round(it["dur"] * FPS)))
    ff = subprocess.Popen([need("ffmpeg"), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                           "-pix_fmt", "yuv420p", seg], stdin=subprocess.PIPE)
    if it["kind"] == "intro":
        base = intro_frame(Path(src_img), cfg_bits["title"], cfg_bits["channel"], size)
        for f in range(nfr):
            tt = f / FPS
            a = min(1, tt / fade_d, (it["dur"] - tt) / fade_d)
            fr = base if a >= 1 else Image.fromarray((np.asarray(base, np.float32) * max(a, 0)).astype(np.uint8))
            ff.stdin.write(fr.tobytes())
        ff.stdin.close(); ff.wait()
        return seg
    src = Image.open(src_img).convert("RGB")
    sh = it["shot"]
    chars = [c for c in sh.get("characters", []) if cfg_bits["puppet"] and puppet.has_pack(c)]
    st = puppet.stage(chars, it["lines"]) if chars else None
    lead_cues = [puppet.cues(l["lead"]) if chars else [] for l in it["lines"]]
    canvas_rgba = src.convert("RGBA") if chars else None
    allc = sh.get("characters", [])
    cpos = {c: (i + 1) / (len(allc) + 1) for i, c in enumerate(allc)}      # approx. left→right placement
    focus = .5
    subs = [(s, e, *sub_image(txt, W, H, H > W)) for s, e, txt in sub_cues(it["lines"], sub_lang)] if sub_lang else []
    fade_in = fade_d if it["new_scene"] else 0
    fade_out = fade_d if it.get("next_new_scene") else 0
    seed = int(h(sh["id"]), 16) % 10000
    for f in range(nfr):
        tt = f / FPS
        li, talking = 0, {}
        for k, l in enumerate(it["lines"]):
            if tt >= l["start"] - .05:
                li = k
            if l["start"] <= tt < l["start"] + l["slot"] and l["speaker"] in chars:
                talking[l["speaker"]] = puppet.shape_at(lead_cues[k], round((tt - l["start"]) * puppet.FPS_ANIM) / puppet.FPS_ANIM)
        sp = it["lines"][li]["speaker"] if it["lines"] else None
        target = cpos.get(sp, sum(cpos.values()) / len(cpos) if cpos else .5)
        focus = target if f == 0 else focus + (target - focus) * min(1, 2.5 / FPS)   # camera eases to the speaker
        if chars:
            frame_src = canvas_rgba.copy()
            puppet.composite(frame_src, chars, st, tt, li, talking, seed)
            frame_src = frame_src.convert("RGB")
        else:
            frame_src = src
        x0, y0, w, hh = window(sh["camera"], tt / it["dur"], src.size, W / H, focus)
        fr = frame_src.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + w, y0 + hh))
        for s, e, im, pos in subs:
            if s <= tt < e:
                fr.paste(im, pos, im)
        a = 1.0
        if fade_in and tt < fade_in:
            a = tt / fade_in
        if fade_out and tt > it["dur"] - fade_out:
            a = min(a, (it["dur"] - tt) / fade_out)
        if a < 1:
            fr = Image.fromarray((np.asarray(fr, np.float32) * max(a, 0)).astype(np.uint8))
        ff.stdin.write(fr.tobytes())
    ff.stdin.close(); ff.wait()
    return seg


def render_picture(cfg, script, tl, img_dir: Path, out: Path, size, sub_lang=None, workers=None) -> Path:
    """Silent video for a timeline."""
    work = cache_dir() / "seg" / h(str(out), tl["total"], size, sub_lang)
    work.mkdir(parents=True, exist_ok=True)
    bits = dict(fade=tempo(cfg)["fade"], puppet=cfg["visuals"]["mode"] == "puppet",
                title=script["title"].get(tl["langs"][0], ""), channel=cfg["channel"]["name"])
    jobs = []
    for n, it in enumerate(tl["items"]):
        img = image_for(it["shot"]["id"], it["scene"]["id"], img_dir)
        if img is None:
            raise RuntimeError(t(f"Görsel yok: {it['shot']['id']}", f"Image missing: {it['shot']['id']}"))
        jobs.append((it, str(img), size, str(work / f"{n:04d}.mp4"), sub_lang, bits))
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 1))
    with Pool(workers) as pool:
        for k, _ in enumerate(pool.imap(render_item, jobs), 1):
            if k % 10 == 0 or k == len(jobs):
                log(t(f"  çekim {k}/{len(jobs)}", f"  shot {k}/{len(jobs)}"))
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{j[3]}'\n" for j in jobs))
    ffmpeg("-f", "concat", "-safe", 0, "-i", lst, "-c", "copy", out)
    return out


def plugin_picture(cfg, script, tl, img_dir, out, size) -> Path:
    p = Path(cfg["visuals"]["plugin"])
    spec = importlib.util.spec_from_file_location("studio_plugin", p)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    mod.render_picture(cfg=cfg, script=script, timeline=tl, images_dir=img_dir, out=out, size=size)
    return out


# ---------------------------------------------------------------- sound
def chime() -> np.ndarray:
    tt = np.arange(int(1.6 * SR)) / SR
    out = np.zeros_like(tt)
    for n, f in enumerate((1318.5, 1568, 2093)):
        s = int(n * .14 * SR)
        x = .14 * np.sin(2 * np.pi * f * tt) * np.exp(-3.5 * tt) * (1 - np.exp(-tt * 200))
        out[s:] += x[:len(out) - s]
    return out


def music_bed(cfg, total: float, seed: str):
    d = cfg["audio"].get("music_dir")
    if not d or not Path(d).is_dir():
        return None
    files = sorted(p for p in Path(d).iterdir() if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg", ".flac"))
    if not files:
        return None
    m = load_audio(random.Random(seed).choice(files))
    n = int(total * SR)
    m = np.tile(m, math.ceil(n / max(1, len(m))))[:n]
    k = int(2 * SR)
    m[:k] *= np.linspace(0, 1, k); m[-k:] *= np.linspace(1, 0, k)
    return m * 10 ** (cfg["audio"].get("music_db", -24) / 20)


def mix(cfg, tl, lang, out_wav: Path, seed: str) -> Path:
    total = tl["total"]
    voice = np.zeros(int((total + 1) * SR), np.float32)
    for it in tl["items"]:
        if it["kind"] == "intro":
            c = chime(); s = int(.3 * SR); voice[s:s + len(c)] += c[:len(voice) - s]
        for l in it["lines"]:
            w = tts.fade(load_audio(Path(l["clips"][lang])))
            s = int((it["start"] + l["start"]) * SR)
            voice[s:s + len(w)] += w[:len(voice) - s]
    voice = voice[:int(total * SR)]
    m = music_bed(cfg, total, seed)
    if m is not None:                       # duck music under speech (-10 dB, smoothed)
        fr = SR // 50
        n = len(voice) // fr
        env = np.sqrt(np.mean(voice[:n * fr].reshape(n, fr) ** 2, axis=1)) > .01
        env = np.convolve(env.astype(float), np.ones(25) / 25, mode="same")
        gain = 1 - .68 * np.clip(env * 2, 0, 1)
        g = np.repeat(gain, fr)
        g = np.concatenate([g, np.full(len(voice) - len(g), g[-1] if len(g) else 1)])
        voice = voice + m[:len(voice)] * g
    voice = voice / max(1.0, np.abs(voice).max() / .95)
    save_wav(out_wav, voice)
    return out_wav


def encode_audio(cfg, wav: Path, out: Path) -> Path:
    """Two-pass EBU R128 loudness normalization (YouTube plays at about -14 LUFS) → AAC."""
    import json as _json
    lufs = cfg["audio"].get("loudness_lufs", -14)
    base = f"loudnorm=I={lufs}:TP=-1.5:LRA=11"
    r = subprocess.run([need("ffmpeg"), "-hide_banner", "-i", str(wav), "-af", base + ":print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    try:
        m = _json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
        flt = (f"{base}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
               f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    except (ValueError, KeyError):
        flt = base
    ffmpeg("-i", wav, "-af", flt, "-ar", SR, "-c:a", "aac", "-b:a", "192k", out)
    return out


def write_srt(tl, lang, out: Path):
    rows, n = [], 1
    for it in tl["items"]:
        for s, e, txt in sub_cues(it["lines"], lang):
            rows.append(f"{n}\n{fmt_ts(it['start'] + s)} --> {fmt_ts(it['start'] + e)}\n{txt}\n"); n += 1
    out.write_text("\n".join(rows), encoding="utf-8")


def mux(picture: Path, audios: list[tuple[str, Path]], out: Path):
    args = ["-i", picture]
    for _, a in audios:
        args += ["-i", a]
    args += ["-map", "0:v"] + sum([["-map", f"{i + 1}:a"] for i in range(len(audios))], [])
    for i, (lg, _) in enumerate(audios):
        args += [f"-metadata:s:a:{i}", f"language={lang_iso3(lg)}", f"-metadata:s:a:{i}", f"title={lang_name(lg)}",
                 f"-disposition:a:{i}", "default" if i == 0 else "0"]
    ffmpeg(*args, "-c", "copy", "-shortest", "-movflags", "+faststart", out)


# ---------------------------------------------------------------- episode
def render_episode(cfg, script: dict, ep: Path) -> dict:
    langs, aspect = cfg.langs, cfg["format"]["aspect"]
    size = (1920, 1080) if aspect == "16:9" else (1080, 1920)
    img_dir = ep / "gorseller"
    burn = cfg["visuals"].get("burn_subtitles")
    groups = [langs] if cfg["channel"]["multi_language"] == "multi_audio" else [[lg] for lg in langs]
    info = dict(videos={}, audio={}, subtitles={}, shorts=[], chapters={})
    picture_fn = plugin_picture if cfg["visuals"]["mode"] == "plugin" else render_picture
    for g in groups:
        tl = timeline(cfg, script, g)
        tag = "" if len(groups) == 1 else f".{g[0]}"
        log(t(f"görüntü: {len(tl['items'])} çekim, {tl['total']:.0f} sn", f"picture: {len(tl['items'])} shots, {tl['total']:.0f} s"))
        pic = picture_fn(cfg, script, tl, img_dir, ep / f"_goruntu{tag}.mp4", size, *([g[0] if burn else None] if picture_fn is render_picture else []))
        tracks = []
        for lg in g:
            wav = mix(cfg, tl, lg, ep / f"_ses.{lg}.wav", script.get("id", ""))
            m4a = encode_audio(cfg, wav, ep / f"ses.{lg}.m4a"); wav.unlink()
            tracks.append((lg, m4a)); info["audio"][lg] = m4a.name
            write_srt(tl, lg, ep / f"altyazi.{lg}.srt"); info["subtitles"][lg] = f"altyazi.{lg}.srt"
            info["chapters"][lg] = [(c[0], c[1].get(lg, "")) for c in tl["chapters"]]
        out = ep / f"video{tag}.mp4"
        mux(pic, tracks, out); pic.unlink()
        for lg in g:
            info["videos"][lg] = out.name
        info["duration"] = tl["total"]
        info["sync_drift"] = tl["drift"]
    info["shorts"] = render_shorts(cfg, script, ep)
    return info


def render_shorts(cfg, script, ep: Path) -> list[dict]:
    out = []
    langs = cfg["youtube"].get("shorts_languages") or cfg.langs
    size = (1080, 1920)
    for n, s in enumerate(script.get("shorts", []), 1):
        for lg in langs:
            tl = trim_short(timeline(cfg, script, [lg], set(s["shot_ids"]), intro=False))
            d = ep / "shorts"; d.mkdir(exist_ok=True)
            pic = render_picture(cfg, script, tl, ep / "gorseller", d / f"_short-{n}.{lg}.mp4", size,
                                 lg if cfg["format"].get("shorts_subtitles", True) else None)
            wav = mix(cfg, tl, lg, d / f"_short-{n}.{lg}.wav", f"{script.get('id')}-s{n}")
            m4a = encode_audio(cfg, wav, d / f"_short-{n}.{lg}.m4a")
            f = d / f"short-{n}.{lg}.mp4"
            mux(pic, [(lg, m4a)], f)
            for x in (pic, wav, m4a):
                x.unlink()
            out.append(dict(n=n, lang=lg, file=f"shorts/{f.name}", duration=tl["total"], title=s["title"]))
    return out
