"""Shared helpers: paths, UI language, .env loading, hashing, ffmpeg wrappers, audio I/O."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(os.environ.get("STUDIO_ROOT", Path(__file__).resolve().parent.parent))
SR = 48000  # internal audio sample rate

# ---------------------------------------------------------------- UI language
_UI = {"lang": os.environ.get("STUDIO_UI_LANG", "tr")}


def set_ui_lang(lang: str) -> None:
    _UI["lang"] = "tr" if str(lang).lower().startswith("tr") else "en"


def t(tr: str, en: str) -> str:
    """Pick the UI string for the current interface language."""
    return tr if _UI["lang"] == "tr" else en


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- paths
def channel_dir() -> Path:
    return Path(os.environ.get("STUDIO_CHANNEL_DIR", ROOT / "kanal"))


def output_dir() -> Path:
    return Path(os.environ.get("STUDIO_OUTPUT_DIR", ROOT / "cikti"))


def cache_dir() -> Path:
    d = Path(os.environ.get("STUDIO_CACHE_DIR", output_dir() / ".cache"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def secrets_dir() -> Path:
    d = Path(os.environ.get("STUDIO_SECRETS_DIR", ROOT / ".gizli"))
    return d


# ---------------------------------------------------------------- secrets (.env)
def load_env(path: Path | None = None) -> None:
    """Load KEY=VALUE lines into os.environ (existing variables win). No external dependency."""
    for p in [path] if path else [ROOT / ".env", secrets_dir() / ".env"]:
        if not p or not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v and k.strip() not in os.environ:
                os.environ[k.strip()] = v


def secret(name: str, required: bool = True) -> str | None:
    v = os.environ.get(name)
    if not v and required:
        raise RuntimeError(t(f"{name} bulunamadı. .env dosyasına ekleyin (bkz. .env.example).",
                             f"{name} is missing. Add it to your .env file (see .env.example)."))
    return v


def set_env_value(name: str, value: str, path: Path | None = None) -> None:
    """Write or replace one KEY=VALUE line in .env (used by the setup wizard)."""
    p = path or ROOT / ".env"
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    lines = [ln for ln in lines if not ln.startswith(name + "=")] + [f"{name}={value}"]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    os.environ[name] = value


# ---------------------------------------------------------------- misc
def h(*a) -> str:
    return hashlib.sha1(json.dumps(a, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()[:16]


def slugify(text: str, n: int = 50) -> str:
    text = text.replace("ı", "i").replace("İ", "I")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:n].strip("-") or "video"


def read_json(p: Path, default=None):
    p = Path(p)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p: Path, data) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def http_json(url: str, body: dict | None = None, headers: dict | None = None, timeout: int = 180,
              retries: int = 5, raw: bool = False, method: str | None = None):
    """POST/GET JSON with retries on 429/5xx/network errors. raw=True returns bytes."""
    data = json.dumps(body).encode() if body is not None else None
    hd = {"Content-Type": "application/json", **(headers or {})}
    last = None
    for n in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hd, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
                return b if raw else json.loads(b or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read()[:400].decode(errors="replace")
            last = RuntimeError(f"HTTP {e.code}: {detail}")
            if e.code not in (408, 409, 429) and e.code < 500:
                raise last from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
        time.sleep(min(60, 2 ** n))
    raise RuntimeError(str(last))


# ---------------------------------------------------------------- ffmpeg / audio
def need(binary: str) -> str:
    p = shutil.which(binary)
    if not p:
        raise RuntimeError(t(f"'{binary}' bulunamadı. Kurulum: README > Gereksinimler.",
                             f"'{binary}' not found. See README > Requirements."))
    return p


def ffmpeg(*args, quiet: bool = True) -> None:
    cmd = [need("ffmpeg"), "-y"] + (["-loglevel", "error"] if quiet else []) + [str(a) for a in args]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("ffmpeg: " + r.stderr[-800:])


_FILTERS: dict[str, bool] = {}


def has_filter(name: str) -> bool:
    if name not in _FILTERS:
        try:
            out = subprocess.run([need("ffmpeg"), "-hide_banner", "-filters"], capture_output=True, text=True).stdout
            _FILTERS[name] = re.search(rf"\s{name}\s", out) is not None
        except RuntimeError:
            _FILTERS[name] = False
    return _FILTERS[name]


def load_audio(path: Path, sr: int = SR) -> np.ndarray:
    """Decode any audio file to mono float32 at `sr` with ffmpeg (proper resampling, no scipy needed)."""
    r = subprocess.run([need("ffmpeg"), "-loglevel", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
                       capture_output=True)
    if r.returncode:
        raise RuntimeError("ffmpeg decode: " + r.stderr.decode(errors="replace")[-400:])
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


def save_wav(path: Path, a: np.ndarray, sr: int = SR) -> None:
    import wave
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    a = np.clip(np.nan_to_num(a), -1, 1)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((a * 32767).astype(np.int16).tobytes())


def pcm16_to_wav(path: Path, pcm: bytes, sr: int) -> None:
    import wave
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)


def probe_duration(path: Path) -> float:
    r = subprocess.run([need("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


# ISO 639-1 → ISO 639-2 (ffmpeg audio stream metadata) and display names
LANGS = {
    "en": ("eng", "English"), "tr": ("tur", "Türkçe"), "de": ("deu", "Deutsch"), "fr": ("fra", "Français"),
    "es": ("spa", "Español"), "it": ("ita", "Italiano"), "pt": ("por", "Português"), "ar": ("ara", "العربية"),
    "ru": ("rus", "Русский"), "hi": ("hin", "हिन्दी"), "ja": ("jpn", "日本語"), "ko": ("kor", "한국어"),
    "zh": ("zho", "中文"), "nl": ("nld", "Nederlands"), "pl": ("pol", "Polski"), "id": ("ind", "Bahasa Indonesia"),
    "az": ("aze", "Azərbaycanca"), "uk": ("ukr", "Українська"), "fa": ("fas", "فارسی"),
}


def lang_iso3(code: str) -> str:
    return LANGS.get(code, (code, code))[0]


def lang_name(code: str) -> str:
    return LANGS.get(code, (code, code))[1]


def fmt_ts(x: float, srt: bool = True) -> str:
    ms = int(round(x * 1000))
    hh, ms = divmod(ms, 3600000)
    mm, ms = divmod(ms, 60000)
    ss, ms = divmod(ms, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}" if srt else (f"{hh}:{mm:02d}:{ss:02d}" if hh else f"{mm}:{ss:02d}")


def python_exe() -> str:
    return sys.executable or "python3"
