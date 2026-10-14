"""Channel configuration: kanal/kanal.yaml (machine settings) + kanal/KANAL.md (human channel bible).

The wizard writes both. Every value has a default here, so a config file only needs what differs.
"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

from .util import channel_dir, set_ui_lang, t

DEFAULTS: dict = {
    "ui_language": "tr",
    "channel": {
        "name": "My Channel",
        "concept": "",
        "type": "kids_story",          # kids_story | education | facts | story | sleep | custom
        "audience": "",
        "made_for_kids": False,
        "languages": ["en"],           # first one is the primary language (lip sync, default audio track)
        "multi_language": "multi_audio",  # multi_audio (one video, extra audio tracks) | separate_videos
        "tone": "",
    },
    "format": {
        "aspect": "16:9",
        "target_minutes": 8,
        "shorts_per_episode": 2,
        "words_per_minute": 130,       # speech pace used to size scripts
        "intro_card": True,
    },
    "script": {
        "mode": "ai",                  # ai | idea | manual
        "approval": True,              # stop after the script until `studio.py onayla <id>`
        "extra_rules": "",
    },
    "llm": {
        "provider": "anthropic",       # anthropic | gemini | openai | none
        "model": "",                   # empty = provider default
        "effort": "medium",
    },
    "characters": [],                  # [{id, name: {lang: name}, description, voice: {id, style}, speed}]
    "narrator": {"enabled": True, "voice": {"id": "", "style": "warm, clear storyteller"}, "speed": 1.0},
    "voice": {
        "provider": "elevenlabs",      # elevenlabs | google | gemini | placeholder
        "model": "",
        "transcript_check": False,     # re-generate a line if its transcript differs (needs Gemini key)
        "match_languages": True,       # re-speak the longer language faster so TR/EN lines stay in sync
    },
    "visuals": {
        "mode": "ai_images",           # ai_images | puppet | local_images | placeholder | plugin
        "style": "bright, colorful 2D cartoon illustration, soft shading, clean lines, child friendly",
        "provider": "gemini",          # gemini | openai
        "model": "",
        "per": "shot",                 # shot | scene  (one image per shot or per scene: cost control)
        "burn_subtitles": False,
        "plugin": "",                  # python file with render_episode(...) when mode == plugin
    },
    "audio": {
        "music_dir": "",               # folder of background music (mp3/wav), empty = no music
        "music_db": -24,
        "loudness_lufs": -14,
        "ambience": False,
    },
    "schedule": {
        "timezone": "Europe/Istanbul",
        "produce": [],                 # [{days: [mon, thu], time: "09:00"}]
        "publish_times": ["17:00"],    # YouTube publish slots (local time)
        "publish_days": [],            # empty = every day
        "min_hours_before_publish": 12,
    },
    "youtube": {
        "enabled": False,
        "privacy": "scheduled",        # scheduled | private | unlisted | public
        "category_id": "1",
        "playlist_id": "",
        "upload_approval": True,       # stop before upload until `studio.py onayla <id>`
        "synthetic_media": True,       # YouTube "altered or synthetic content" disclosure
        "upload_captions": True,
        "upload_thumbnail": True,
        "upload_shorts": True,
    },
    "seo": {
        "keywords": [],
        "hashtags": [],
        "default_tags": [],
        "description_footer": {},
    },
    "google": {"endpoint": "aistudio"},  # aistudio (generativelanguage) | vertex (aiplatform, express key)
}

CHANNEL_TYPES = {
    "kids_story": ("Çocuk hikayeleri / animasyon", "Kids stories / animation"),
    "education": ("Eğitici anlatım", "Educational explainers"),
    "facts": ("İlginç bilgiler / liste videoları", "Facts / list videos"),
    "story": ("Hikaye, masal, anlatı (yetişkin)", "Storytelling (adult)"),
    "sleep": ("Uyku masalı / rahatlatıcı", "Sleep stories / relaxing"),
    "custom": ("Özel (kendi tanımım)", "Custom (my own description)"),
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config(dict):
    """dict with attribute-free helpers. Access: cfg['channel']['languages']."""

    @property
    def langs(self) -> list[str]:
        return list(self["channel"]["languages"]) or ["en"]

    @property
    def primary(self) -> str:
        return self.langs[0]

    def character(self, cid: str) -> dict | None:
        for c in self["characters"]:
            if c["id"] == cid:
                return c
        return None

    def speaker_ids(self) -> list[str]:
        ids = [c["id"] for c in self["characters"]]
        return ids + (["narrator"] if self["narrator"].get("enabled", True) else [])

    def bible(self) -> str:
        p = channel_dir() / "KANAL.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""


def path() -> Path:
    return channel_dir() / "kanal.yaml"


def load(required: bool = True) -> Config:
    p = path()
    if not p.exists():
        if required:
            raise SystemExit(t("Kanal ayarı yok. Önce kurulumu çalıştırın:  python studio.py kurulum",
                               "No channel config yet. Run the setup first:  python studio.py setup"))
        return Config(copy.deepcopy(DEFAULTS))
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    cfg = Config(_merge(DEFAULTS, data))
    set_ui_lang(cfg["ui_language"])
    validate(cfg)
    return cfg


def save(cfg: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(dict(cfg), allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")


def validate(cfg: Config) -> None:
    errs = []
    if cfg["script"]["mode"] not in ("ai", "idea", "manual"):
        errs.append("script.mode")
    if cfg["voice"]["provider"] not in ("elevenlabs", "google", "gemini", "placeholder"):
        errs.append("voice.provider")
    if cfg["visuals"]["mode"] not in ("ai_images", "puppet", "local_images", "placeholder", "plugin"):
        errs.append("visuals.mode")
    if cfg["format"]["aspect"] not in ("16:9", "9:16"):
        errs.append("format.aspect")
    if cfg["channel"]["multi_language"] not in ("multi_audio", "separate_videos"):
        errs.append("channel.multi_language")
    ids = [c.get("id") for c in cfg["characters"]]
    if len(ids) != len(set(ids)) or "narrator" in ids:
        errs.append("characters[].id (unique, not 'narrator')")
    if errs:
        raise SystemExit(t("kanal.yaml içinde geçersiz değer: ", "Invalid value in kanal.yaml: ") + ", ".join(errs))
