"""Voice generation: one audio clip per line and language, cached, cleaned and level-matched.

Providers: elevenlabs | google (Cloud TTS, Chirp 3 HD) | gemini (Gemini TTS) | placeholder (offline test voice).
Multi-language channels: every line gets one shared time slot. If one language runs much longer, it is re-spoken
faster (provider speed setting), then the shorter one is stretched a little (pitch preserved), so lip sync and
visuals stay in step for every audio track.
"""
from __future__ import annotations

import base64
import difflib
import json
import os
import re
import subprocess
import shutil
from pathlib import Path

import numpy as np

from .util import (SR, cache_dir, ffmpeg, has_filter, http_json, load_audio, log, pcm16_to_wav, save_wav, secret, t)

LOCALES = {"en": "en-US", "tr": "tr-TR", "de": "de-DE", "fr": "fr-FR", "es": "es-ES", "it": "it-IT", "pt": "pt-BR",
           "ar": "ar-XA", "ru": "ru-RU", "hi": "hi-IN", "ja": "ja-JP", "ko": "ko-KR", "zh": "cmn-CN", "nl": "nl-NL",
           "pl": "pl-PL", "id": "id-ID", "uk": "uk-UA"}
DEFAULT_MODELS = {"elevenlabs": "eleven_v4", "gemini": "gemini-3.8-flash-tts", "google": "chirp3-hd"}
SPEED_RANGE = {"elevenlabs": (0.7, 1.2), "google": (0.5, 1.6), "placeholder": (0.5, 2.0)}
MAX_STRETCH = 1.12      # a shorter language is slowed down by at most 12 %
MAX_SPEEDUP = 1.08      # the longest language is sped up by at most 8 %
MATCH_RATIO = 1.12      # re-speak a language faster when it is >12 % longer than the primary
USAGE: list[dict] = []  # billed characters per request (cost report)


# ---------------------------------------------------------------- text
def speakable(text: str) -> str:
    text = re.sub(r"[\"“”*_#]", "", text)
    text = text.replace("…", "... ").replace("—", ", ").replace(" - ", ", ")
    return re.sub(r"\s+", " ", text).strip()


def _tag(emotion: str) -> str:
    e = re.sub(r"[^a-zA-Z ,-]", "", emotion or "").strip().lower()
    return " ".join(f"[{w.strip()}]" for w in e.split(",")[:2] if w.strip() and len(w.split()) <= 3)


# ---------------------------------------------------------------- providers → raw audio file
def _elevenlabs(cfg, voice_id, text, emotion, lang, speed, raw: Path) -> int:
    model = cfg["voice"].get("model") or DEFAULT_MODELS["elevenlabs"]
    t_ = speakable(text)
    if emotion and re.search(r"v3|v4", model):          # expressive models read [tags] as acting directions
        t_ = f"{_tag(emotion)} {t_}".strip()
    body = dict(text=t_, model_id=model, language_code=lang, voice_settings=dict(speed=round(speed, 2)))
    pcm = http_json(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=pcm_24000", body,
                    {"xi-api-key": secret("ELEVENLABS_API_KEY")}, raw=True, timeout=180)
    pcm16_to_wav(raw, pcm, 24000)
    return len(t_)


def _google(cfg, voice_id, text, emotion, lang, speed, raw: Path) -> int:
    loc = LOCALES.get(lang, lang)
    name = voice_id if "-" in voice_id else f"{loc}-Chirp3-HD-{voice_id}"
    body = dict(input=dict(text=speakable(text)), voice=dict(languageCode=loc, name=name),
                audioConfig=dict(audioEncoding="LINEAR16", sampleRateHertz=24000, speakingRate=round(speed, 2)))
    r = http_json("https://texttospeech.googleapis.com/v1/text:synthesize", body,
                  {"x-goog-api-key": secret("GOOGLE_TTS_API_KEY")}, timeout=120)
    raw.write_bytes(base64.b64decode(r["audioContent"]))
    return len(speakable(text))


def _gemini(cfg, voice_id, text, emotion, lang, speed, raw: Path, style="") -> int:
    from .llm import gemini_url
    model = cfg["voice"].get("model") or DEFAULT_MODELS["gemini"]
    direction = ", ".join(x for x in [style, emotion, "slightly faster" if speed > 1.05 else ""] if x)
    prompt = f"[{direction}] {speakable(text)}" if direction else speakable(text)   # bracketed directions are not read aloud
    url, hd = gemini_url(cfg, model, secret("GEMINI_API_KEY"))
    body = dict(contents=[dict(role="user", parts=[dict(text=prompt)])],
                generationConfig=dict(responseModalities=["AUDIO"],
                                      speechConfig=dict(voiceConfig=dict(prebuiltVoiceConfig=dict(voiceName=voice_id)))))
    r = http_json(url, body, hd, timeout=180)
    pcm = base64.b64decode(r["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
    pcm16_to_wav(raw, pcm, 24000)
    return len(prompt)


def _placeholder(cfg, voice_id, text, emotion, lang, speed, raw: Path) -> int:
    """Offline test voice: espeak-ng when installed, otherwise a syllable-rhythm 'babble' so timing is realistic."""
    words = speakable(text).split()
    if shutil.which("espeak-ng"):
        v = {"tr": "tr", "en": "en-us"}.get(lang, lang)
        pitch = 30 + (sum(map(ord, voice_id or "x")) % 60)
        subprocess.run(["espeak-ng", "-v", v, "-s", str(int(155 * speed)), "-p", str(pitch), "-w", str(raw), " ".join(words)], check=True)
        return len(text)
    rnd = np.random.default_rng(int(_key(voice_id, text), 16) % 2**32)
    base = 140 + (sum(map(ord, voice_id or "x")) % 160)
    out = []
    for w in words:
        for _ in range(max(1, round(len(w) / 3))):
            d = rnd.uniform(.13, .2) / speed
            tt = np.arange(int(d * 24000)) / 24000
            f = base * (1 + .15 * np.sin(2 * np.pi * 3 * tt)) * rnd.uniform(.9, 1.15)
            env = np.sin(np.pi * tt / d) ** 1.5
            out.append(.25 * env * (np.sin(2 * np.pi * np.cumsum(f) / 24000) + .3 * np.sin(4 * np.pi * np.cumsum(f) / 24000)))
        out.append(np.zeros(int(.09 / speed * 24000)))
    pcm16_to_wav(raw, (np.concatenate(out) * 32767).astype(np.int16).tobytes(), 24000)
    return len(text)


PROVIDERS = {"elevenlabs": _elevenlabs, "google": _google, "gemini": _gemini, "placeholder": _placeholder}


# ---------------------------------------------------------------- cleanup
def _frames_rms(a, fr):
    n = len(a) // fr
    return np.sqrt(np.mean(a[:n * fr].reshape(n, fr) ** 2, axis=1)) if n else np.zeros(0)


def gemini_tail_fix(a: np.ndarray, sr: int = SR) -> np.ndarray:
    """Gemini TTS appends a ~0.18 s corrupt burst after speech (found in production: crackle on every line).
    Drop a short, isolated, DC-heavy or over-loud last segment."""
    fr = int(.01 * sr)
    r = _frames_rms(a, fr)
    voiced = r >= .01
    segs, i, n = [], 0, len(r)
    while i < n:
        if not voiced[i]:
            i += 1; continue
        j = i
        while j < n and (voiced[j] or (j + 1 < n and voiced[j + 1])):
            j += 1
        segs.append((i * fr, j * fr)); i = j
    if len(segs) < 2:
        return a
    speech = np.median(r[voiced])
    end = len(a)
    for k in range(3):
        if len(segs) < 2:
            break
        s, e = segs[-1]
        x = a[s:e]
        dc = abs(x.mean()) / (x.std() + 1e-9) > .2
        loud = np.sqrt(np.mean(x ** 2)) > 1.5 * speech
        gap = (s - segs[-2][1]) >= .04 * sr
        if not (((e - s) <= .42 * sr and dc) or ((e - s) <= .26 * sr and loud and gap and k == 0)):
            break
        end = segs[-2][1]; segs.pop()
    return a[:end]


def fade(a: np.ndarray, ms: float = 8, sr: int = SR) -> np.ndarray:
    n = min(int(ms / 1000 * sr), len(a) // 2)
    if n > 1:
        a = a.copy(); r = np.linspace(0, 1, n); a[:n] *= r; a[-n:] *= r[::-1]
    return a


def shorten_silences(a: np.ndarray, longest: float = .6, sr: int = SR) -> np.ndarray:
    """Cap pauses inside a line (models sometimes wait 2-10 s) with 10 ms crossfades at the cuts."""
    fr = int(.02 * sr)
    r = _frames_rms(a, fr)
    if len(r) < 3:
        return a
    quiet = r < max(r.max() * .04, 1e-4)
    m = int(longest / .02)
    parts, i, n = [], 0, len(r)
    while i < n:
        j = i
        while j < n and quiet[j] == quiet[i]:
            j += 1
        if quiet[i] and j - i > m:
            parts.append(a[i * fr:(i + m // 2) * fr]); parts.append(None); parts.append(a[(j - (m - m // 2)) * fr:j * fr])
        else:
            parts.append(a[i * fr:j * fr])
        i = j
    parts.append(a[n * fr:])
    out, x = np.zeros(0, np.float32), int(.01 * sr)
    cut = False
    for p in parts:
        if p is None:
            cut = True; continue
        if cut and len(out) and len(p):
            k = min(x, len(out), len(p)); w = np.linspace(0, 1, k)
            out = np.concatenate([out[:-k], out[-k:] * (1 - w) + p[:k] * w, p[k:]]); cut = False
        else:
            out = np.concatenate([out, p])
    return out


def finish(raw: Path, out: Path, provider: str, narrator: bool) -> float:
    a = load_audio(raw)                       # → 48 kHz mono float (ffmpeg resampler, no aliasing hiss)
    if provider == "gemini":
        a = gemini_tail_fix(a)
    nz = np.where(np.abs(a) > .01)[0]
    if len(nz):
        a = a[max(0, nz[0] - int(.02 * SR)): nz[-1] + int(.06 * SR)]
    a = shorten_silences(a)
    rms = np.sqrt(np.mean(a ** 2)) + 1e-6
    a = a * min((.15 if narrator else .12) / rms, .9 / (np.abs(a).max() + 1e-6))   # equal loudness between voices
    save_wav(out, fade(a, 8))
    return len(a) / SR


# ---------------------------------------------------------------- transcript check (optional)
def similarity(a: str, b: str) -> float:
    n = lambda s: re.sub(r"[^\w ]", "", s.lower()).split()
    return difflib.SequenceMatcher(None, n(a), n(b)).ratio()


def transcribe(cfg, wav: Path) -> str | None:
    """Gemini transcription. None = check unavailable (then the line is accepted, never re-billed)."""
    if not os.environ.get("GEMINI_API_KEY"):
        return None
    from .llm import gemini_url
    try:
        url, hd = gemini_url(cfg, "gemini-3.8-flash", os.environ["GEMINI_API_KEY"])
        r = http_json(url, dict(contents=[dict(role="user", parts=[
            dict(inlineData=dict(mimeType="audio/wav", data=base64.b64encode(wav.read_bytes()).decode())),
            dict(text="Transcribe this audio word for word in its original language. Output only the transcript.")])]), hd, retries=2)
        return r["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return None


# ---------------------------------------------------------------- public API
def voice_of(cfg, speaker: str) -> tuple[str, str, float, bool]:
    """→ (voice id, style, speed, is_narrator)"""
    if speaker == "narrator":
        n = cfg["narrator"]
        return n.get("voice", {}).get("id", ""), n.get("voice", {}).get("style", ""), float(n.get("speed", 1.0)), True
    c = cfg.character(speaker) or {}
    return c.get("voice", {}).get("id", ""), c.get("voice", {}).get("style", ""), float(c.get("speed", 1.0)), False


def synth(cfg, speaker: str, text: str, emotion: str, lang: str, speed_mul: float = 1.0) -> dict:
    """One line in one language → {wav, dur}. Cached by every input that changes the sound."""
    prov = cfg["voice"]["provider"]
    vid, style, speed, narrator = voice_of(cfg, speaker)
    if prov != "placeholder" and not vid:
        raise RuntimeError(t(f"'{speaker}' için ses kimliği (voice id) yok. kanal.yaml > characters/narrator > voice.id",
                             f"No voice id for '{speaker}'. Set it in kanal.yaml > characters/narrator > voice.id"))
    lo, hi = SPEED_RANGE.get(prov, (1, 1))
    sp = min(hi, max(lo, speed * speed_mul))
    key = _key(prov, cfg["voice"].get("model"), vid, text, emotion, lang, round(sp, 3), style)
    d = cache_dir() / "tts"; d.mkdir(parents=True, exist_ok=True)
    out, meta = d / f"{key}.wav", d / f"{key}.json"
    if meta.exists() and out.exists():
        return json.loads(meta.read_text())
    raw = d / f"{key}.raw.wav"
    best = None
    tries = 3 if cfg["voice"].get("transcript_check") else 1
    for n in range(tries):
        kw = dict(style=style) if prov == "gemini" else {}
        chars = PROVIDERS[prov](cfg, vid or speaker, text, emotion if n < 2 else "", lang, sp, raw, **kw)
        USAGE.append(dict(provider=prov, speaker=speaker, lang=lang, chars=chars))
        score = 1.0
        if tries > 1:
            tr = transcribe(cfg, raw)
            score = 1.0 if tr is None else similarity(text, tr)
        if best is None or score > best:
            best = score; os.replace(raw, str(raw) + ".best")
        if score >= .85:
            break
    os.replace(str(raw) + ".best", raw)
    dur = finish(raw, out, prov, narrator)
    raw.unlink(missing_ok=True)
    res = dict(wav=str(out), dur=dur, score=round(best, 2))
    meta.write_text(json.dumps(res))
    return res


def _key(*a):
    from .util import h
    return h("tts2", *a)


def _speed_file() -> Path:
    return cache_dir() / "tts" / "speed_support.json"


def speed_works(cfg) -> bool:
    """Some models ignore the speed setting (seen with ElevenLabs v4). Learned once, remembered in the cache."""
    k = f"{cfg['voice']['provider']}/{cfg['voice'].get('model') or ''}"
    d = json.loads(_speed_file().read_text()) if _speed_file().exists() else {}
    return d.get(k, True)


def _remember_speed(cfg, ok: bool) -> None:
    k = f"{cfg['voice']['provider']}/{cfg['voice'].get('model') or ''}"
    p = _speed_file(); p.parent.mkdir(parents=True, exist_ok=True)
    d = json.loads(p.read_text()) if p.exists() else {}
    d[k] = ok
    p.write_text(json.dumps(d))


def retime(wav: str, factor: float) -> str:
    """factor > 1 slows down, < 1 speeds up; pitch preserved."""
    out = wav[:-4] + f"_x{factor:.3f}.wav"
    if not os.path.exists(out):
        flt = f"rubberband=tempo={1 / factor:.4f}" if has_filter("rubberband") else f"atempo={1 / factor:.4f}"
        ffmpeg("-i", wav, "-af", flt, "-ar", SR, "-ac", 1, out)
    return out


stretch = retime


def voice_line(cfg, line: dict, langs: list[str]) -> dict:
    """All languages of one line in a shared slot → {slot, clips: {lang: wav}, lead, drift}.
    The first language in `langs` leads (lip sync). Steps: (1) re-speak a much longer/shorter language at another
    speed when the provider honours it, (2) speed the longest clip up ≤ 8 % and slow shorter ones ≤ 12 %."""
    P = langs[0]
    clips = {lg: synth(cfg, line["speaker"], line["text"][lg], line["emotion"], lg) for lg in langs}
    prov = cfg["voice"]["provider"]
    if len(langs) > 1 and cfg["voice"].get("match_languages", True) and prov in SPEED_RANGE and speed_works(cfg):
        p = clips[P]["dur"]
        for lg in langs[1:]:
            r = clips[lg]["dur"] / max(p, .1)
            if r > MATCH_RATIO or r < 1 / MATCH_RATIO:
                mul = min(r / 1.04, 1.18) if r > 1 else max(r * 1.04, .88)
                alt = synth(cfg, line["speaker"], line["text"][lg], line["emotion"], lg, speed_mul=mul)
                if abs(alt["dur"] - clips[lg]["dur"]) < .03 * clips[lg]["dur"]:
                    _remember_speed(cfg, False)          # the model ignored the speed setting: stop paying for retries
                if abs(alt["dur"] - p) < abs(clips[lg]["dur"] - p):
                    clips[lg] = alt
    durs = [c["dur"] for c in clips.values()]
    slot = max(max(durs) / MAX_SPEEDUP, min(max(durs), min(durs) * MAX_STRETCH))
    out, ends = {}, []
    for lg, c in clips.items():
        f = min(max(slot / c["dur"], 1 / MAX_SPEEDUP), MAX_STRETCH)
        out[lg] = retime(c["wav"], f) if abs(f - 1) > .005 else c["wav"]
        ends.append(c["dur"] * f)
    slot = max(ends)
    return dict(slot=slot, clips=out, lead=out[P], drift=round(max(ends) - min(ends), 2))


def cost_summary(cfg) -> dict:
    chars = sum(u["chars"] for u in USAGE)
    return dict(provider=cfg["voice"]["provider"], requests=len(USAGE), characters=chars)
