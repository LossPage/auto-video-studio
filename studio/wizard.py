"""First-run setup: asks about the channel and writes kanal/kanal.yaml, kanal/KANAL.md, .env and the content folders.

Interactive by default. `python studio.py kurulum --cevaplar cevaplar.yaml` takes answers from a file (keys are the
question ids below); anything missing falls back to the default, so CI and scripted installs work too.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

from . import config, llm
from .config import CHANNEL_TYPES, DEFAULTS
from .util import ROOT, channel_dir, http_json, set_env_value, set_ui_lang, slugify, t

TYPE_DEFAULTS = {  # minutes, made_for_kids, category, style
    "kids_story": (8, True, "1", "bright, colorful 2D cartoon illustration for young children, soft shading, rounded shapes, clean outlines"),
    "education": (8, False, "27", "clean modern flat illustration, clear composition, friendly infographic feel"),
    "facts": (6, False, "24", "vivid cinematic digital illustration, dramatic lighting, high detail"),
    "story": (12, False, "24", "atmospheric painterly illustration, cinematic lighting, rich colors"),
    "sleep": (20, False, "24", "soft dreamy watercolor illustration, pastel night colors, calm and cozy"),
    "custom": (8, False, "24", "high quality digital illustration"),
}
GOOGLE_VOICES = "Aoede, Kore, Leda, Zephyr, Callirrhoe, Autonoe, Despina, Erinome, Laomedeia, Achernar, Gacrux, Sulafat (female) · " \
                "Puck, Charon, Fenrir, Orus, Enceladus, Iapetus, Umbriel, Algieba, Algenib, Rasalgethi, Alnilam, Sadachbia (male)"


class Asker:
    def __init__(self, answers: dict | None):
        self.a = answers or {}
        self.interactive = answers is None and sys.stdin.isatty()

    def _get(self, key, prompt, default):
        if key in self.a:
            return self.a[key]
        if not self.interactive:
            return default
        d = "" if default in (None, "") else f" [{default}]"
        v = input(f"\n{prompt}{d}: ").strip()
        return v if v else default

    def text(self, key, prompt, default=""):
        return self._get(key, prompt, default)

    def secret(self, key, prompt, env_name):
        if os.environ.get(env_name):
            print(t(f"  {env_name} zaten tanımlı, kullanılıyor.", f"  {env_name} already set, using it."))
            return os.environ[env_name]
        if key in self.a:
            return self.a[key]
        if not self.interactive:
            return ""
        import getpass
        return getpass.getpass(f"\n{prompt} ({t('boş = sonra', 'empty = later')}): ").strip()

    def number(self, key, prompt, default):
        v = self._get(key, prompt, default)
        try:
            return float(v) if "." in str(v) else int(v)
        except ValueError:
            return default

    def yes(self, key, prompt, default=True):
        v = self._get(key, prompt + t(" (e/h)", " (y/n)"), t("e", "y") if default else t("h", "n"))
        return v if isinstance(v, bool) else str(v).lower()[:1] in ("e", "y", "1", "t")

    def choose(self, key, prompt, options: list[tuple[str, str]], default: str):
        if key in self.a:
            return self.a[key]
        if not self.interactive:
            return default
        print(f"\n{prompt}")
        for i, (k, label) in enumerate(options, 1):
            print(f"  {i}) {label}" + ("  ←" if k == default else ""))
        v = input(t("Seçim", "Choice") + f" [{[k for k, _ in options].index(default) + 1}]: ").strip()
        if v.isdigit() and 1 <= int(v) <= len(options):
            return options[int(v) - 1][0]
        return v if v in [k for k, _ in options] else default

    def items(self, key, prompt, default: list):
        v = self._get(key, prompt, ", ".join(default))
        return v if isinstance(v, list) else [x.strip() for x in str(v).split(",") if x.strip()]


def _bi(tr, en):
    return t(tr, en)


# ---------------------------------------------------------------- AI channel design
DESIGN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["concept", "audience", "tone", "visual_style", "characters", "narrator_style", "keywords", "hashtags", "writing_rules", "ideas"],
    "properties": {
        "concept": {"type": "string"}, "audience": {"type": "string"}, "tone": {"type": "string"},
        "visual_style": {"type": "string"}, "narrator_style": {"type": "string"},
        "characters": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                  "required": ["name", "description", "voice_style"],
                                                  "properties": {"name": {"type": "string"}, "description": {"type": "string"},
                                                                 "voice_style": {"type": "string"}}}},
        "keywords": {"type": "array", "items": {"type": "string"}}, "hashtags": {"type": "array", "items": {"type": "string"}},
        "writing_rules": {"type": "string"}, "ideas": {"type": "array", "items": {"type": "string"}},
    },
}


def ai_design(cfg, brief: str) -> dict:
    sys_p = ("You design successful YouTube channels. Given a short brief, propose a complete, specific channel concept. "
             "Characters: 0-5 memorable, visually distinct recurring characters (none if the format does not need them), "
             "each with a precise visual description an image model can reproduce (species/age, colors, clothing, props) and "
             "a voice style for text-to-speech (age, gender, energy, pace). visual_style: one reusable image-style prompt. "
             "keywords: 10-15 search phrases in the channel's languages. ideas: 10 strong first episode ideas, one line each. "
             f"Write descriptions and visual prompts in English; write concept, audience, tone, writing_rules and ideas in "
             f"{'Turkish' if cfg['ui_language'] == 'tr' else 'English'}.")
    prompt = (f"Brief: {brief}\nChannel type: {cfg['channel']['type']}\nLanguages: {', '.join(cfg['channel']['languages'])}\n"
              f"Made for kids: {cfg['channel']['made_for_kids']}\nTarget length: {cfg['format']['target_minutes']} min")
    return llm.generate(cfg, sys_p, prompt, DESIGN_SCHEMA, max_tokens=16000)


# ---------------------------------------------------------------- voices
def elevenlabs_voices() -> list[dict]:
    try:
        r = http_json("https://api.elevenlabs.io/v2/voices?page_size=100", None, {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
                      retries=1, method="GET")
        return [dict(id=v["voice_id"], name=v["name"], labels=", ".join(f"{v2}" for v2 in (v.get("labels") or {}).values()))
                for v in r.get("voices", [])]
    except Exception as e:
        print(t(f"  ses listesi alınamadı: {e}", f"  could not list voices: {e}"))
        return []


def pick_voice(ask: Asker, key: str, who: str, style: str, provider: str, voices: list[dict]) -> str:
    if provider == "placeholder":
        return ""
    if provider == "elevenlabs" and voices and ask.interactive and key not in ask.a:
        print(t(f"\n{who} için ses ({style}):", f"\nVoice for {who} ({style}):"))
        for i, v in enumerate(voices[:40], 1):
            print(f"  {i:2d}) {v['name']}  — {v['labels']}")
        v = input(t("Numara ya da voice_id (boş = sonra)", "Number or voice_id (empty = later)") + ": ").strip()
        if v.isdigit() and 1 <= int(v) <= len(voices[:40]):
            return voices[int(v) - 1]["id"]
        return v
    hint = GOOGLE_VOICES if provider in ("google", "gemini") else "voice_id"
    return ask.text(key, t(f"{who} için ses ({style}). Seçenekler: {hint}", f"Voice for {who} ({style}). Options: {hint}"), "")


# ---------------------------------------------------------------- main
def run(answers_file: str | None = None) -> None:
    answers = yaml.safe_load(Path(answers_file).read_text(encoding="utf-8")) if answers_file else None
    ask = Asker(answers)
    cfg = config.load(required=False)
    if config.path().exists() and ask.interactive and not ask.yes("overwrite", t("Mevcut kanal ayarı var. Üzerine yazılsın mı?",
                                                                                     "A channel config exists. Overwrite?"), False):
        return
    ui = ask.choose("ui_language", "Arayüz dili / Interface language", [("tr", "Türkçe"), ("en", "English")], "tr")
    cfg["ui_language"] = ui; set_ui_lang(ui)
    print(t("\n=== 1/9 Kanal ===", "\n=== 1/9 Channel ==="))
    ch = cfg["channel"]
    ch["name"] = ask.text("name", t("Kanal adı", "Channel name"), "My Channel")
    ch["type"] = ask.choose("type", t("Kanal türü", "Channel type"), [(k, t(*v)) for k, v in CHANNEL_TYPES.items()], "kids_story")
    mins, kids, cat, style = TYPE_DEFAULTS[ch["type"]]
    ch["concept"] = ask.text("concept", t("Konsept: kanalı 1-3 cümleyle anlatın (ne anlatıyor, kime, neden izlenir?)",
                                          "Concept: describe the channel in 1-3 sentences (what, for whom, why watch?)"), "")
    ch["languages"] = ask.items("languages", t("Video dilleri (ilk = ana dil), ör. en, tr", "Video languages (first = main), e.g. en, tr"), ["en"])
    if len(ch["languages"]) > 1:
        ch["multi_language"] = ask.choose("multi_language", t("Birden çok dil nasıl yayınlansın?", "How to publish several languages?"),
                                          [("multi_audio", t("Tek video, her dil ayrı ses kanalı (YouTube çok dilli ses)", "One video, one audio track per language")),
                                           ("separate_videos", t("Her dil ayrı video", "A separate video per language"))], "multi_audio")
    ch["made_for_kids"] = ask.yes("made_for_kids", t("Çocuklara özel mi? (COPPA: yorum ve kişiselleştirilmiş reklam kapanır)",
                                                     "Made for kids? (COPPA: comments and personalized ads turn off)"), kids)
    ch["audience"] = ask.text("audience", t("Hedef kitle (ör. 4-7 yaş)", "Audience (e.g. ages 4-7)"), "")

    print(t("\n=== 2/9 Format ===", "\n=== 2/9 Format ==="))
    f = cfg["format"]
    f["target_minutes"] = ask.number("target_minutes", t("Bölüm süresi (dakika)", "Episode length (minutes)"), mins)
    f["shorts_per_episode"] = ask.number("shorts_per_episode", t("Bölüm başına Shorts sayısı", "Shorts per episode"), 2)
    f["aspect"] = ask.choose("aspect", t("Ana video yönü", "Main video orientation"),
                             [("16:9", t("Yatay 16:9 (normal YouTube)", "Landscape 16:9")), ("9:16", t("Dikey 9:16 (yalnız Shorts kanalı)", "Vertical 9:16 (Shorts-only)"))], "16:9")
    if ch["type"] == "sleep":
        f["words_per_minute"] = 105

    print(t("\n=== 3/9 Yapay zeka (senaryo, SEO) ===", "\n=== 3/9 AI (scripts, SEO) ==="))
    lp = ask.choose("llm_provider", t("Metin yazan yapay zeka", "AI for writing"),
                    [("anthropic", "Claude (Anthropic)"), ("gemini", "Gemini (Google)"), ("openai", "OpenAI"),
                     ("none", t("Yok (senaryoları ben yazarım)", "None (I write scripts myself)"))], "anthropic")
    cfg["llm"]["provider"] = lp
    if lp != "none":
        key = ask.secret(f"{lp}_key", t(f"{llm.KEY_NAMES[lp]} anahtarı", f"{llm.KEY_NAMES[lp]} key"), llm.KEY_NAMES[lp])
        if key:
            set_env_value(llm.KEY_NAMES[lp], key)
        cfg["llm"]["model"] = ask.text("llm_model", t("Model (boş = önerilen)", "Model (empty = recommended)"), llm.DEFAULT_MODELS[lp])
    if lp == "gemini":
        cfg["google"]["endpoint"] = ask.choose("google_endpoint", t("Google anahtar türü", "Google key type"),
                                               [("aistudio", "AI Studio (aistudio.google.com)"), ("vertex", "Vertex AI / Agent Platform (express key)")], "aistudio")

    design = {}
    if llm.available(cfg) and ask.yes("ai_design", t("Konsepti yapay zeka detaylandırsın mı? (karakterler, stil, anahtar kelimeler, fikirler)",
                                                    "Let the AI flesh out the concept? (characters, style, keywords, ideas)"), True):
        try:
            design = ai_design(cfg, ch["concept"] or ch["name"])
            print(t("\nÖneri:", "\nProposal:"))
            print(f"  {design['concept']}\n  " + t("Karakterler: ", "Characters: ") + ", ".join(c["name"] for c in design["characters"]))
            if not ask.yes("ai_design_ok", t("Bu öneriyle devam edilsin mi? (sonra KANAL.md'den değiştirebilirsiniz)",
                                             "Continue with this proposal? (editable later in KANAL.md)"), True):
                design = {}
        except Exception as e:
            print(t(f"  Yapay zeka önerisi alınamadı: {e}", f"  AI proposal failed: {e}"))
    if design:
        ch["concept"] = design["concept"]
        ch["audience"] = ch["audience"] or design["audience"]
        ch["tone"] = design["tone"]
        cfg["script"]["extra_rules"] = design["writing_rules"]

    print(t("\n=== 4/9 Senaryo ===", "\n=== 4/9 Scripts ==="))
    modes = [("ai", t("Yapay zeka konuyu da kendisi seçsin ve yazsın", "AI picks the topic and writes everything")),
             ("idea", t("Ben fikir veririm (kanal/icerik/fikirler.md), yapay zeka yazar", "I give ideas (kanal/icerik/fikirler.md), AI writes")),
             ("manual", t("Senaryoları ben yazarım (kanal/icerik/senaryolar/*.md)", "I write the scripts (kanal/icerik/senaryolar/*.md)"))]
    cfg["script"]["mode"] = ask.choose("script_mode", t("Senaryolar nasıl oluşsun?", "How are scripts made?"),
                                       modes if lp != "none" else modes[2:], "idea" if lp != "none" else "manual")
    if cfg["script"]["mode"] == "manual" and len(ch["languages"]) > 1:
        cfg["script"]["written_language"] = ask.text("written_language", t("Senaryoları hangi dilde yazacaksınız?", "Which language will you write in?"),
                                                     ui if ui in ch["languages"] else ch["languages"][0])
    cfg["script"]["approval"] = ask.yes("script_approval", t("Senaryo görsele geçmeden önce onayımı beklesin mi?", "Wait for my approval before visuals?"), True)

    print(t("\n=== 5/9 Karakterler ===", "\n=== 5/9 Characters ==="))
    chars = []
    proposed = design.get("characters") or []
    n = ask.number("character_count", t("Kaç tekrar eden karakter olacak? (0 = yalnız anlatıcı)", "How many recurring characters? (0 = narrator only)"),
                   len(proposed) if design else (2 if ch["type"] == "kids_story" else 0))
    for i in range(int(n)):
        p = proposed[i] if i < len(proposed) else {}
        name = ask.text(f"char{i + 1}_name", t(f"{i + 1}. karakterin adı", f"Character {i + 1} name"), p.get("name", ""))
        if not name:
            continue
        desc = ask.text(f"char{i + 1}_description", t(f"{name}: görünüş (İngilizce önerilir: tür, renkler, kıyafet)",
                                                      f"{name}: look (species, colors, clothing)"), p.get("description", ""))
        vstyle = ask.text(f"char{i + 1}_voice_style", t(f"{name}: ses tarifi (yaş, cinsiyet, enerji)", f"{name}: voice (age, gender, energy)"),
                          p.get("voice_style", ""))
        cid = slugify(name, 20).replace("-", "_") or f"char{i + 1}"
        chars.append(dict(id=cid, name={lg: name for lg in ch["languages"]}, description=desc, voice=dict(id="", style=vstyle), speed=1.0))
    cfg["characters"] = chars
    cfg["narrator"]["enabled"] = ask.yes("narrator", t("Anlatıcı (dış ses) olsun mu?", "Use a narrator voice?"), True)
    cfg["narrator"]["voice"]["style"] = design.get("narrator_style") or cfg["narrator"]["voice"]["style"]

    print(t("\n=== 6/9 Görseller ===", "\n=== 6/9 Visuals ==="))
    v = cfg["visuals"]
    v["mode"] = ask.choose("visual_mode", t("Görüntü nasıl üretilsin?", "How are pictures made?"),
                           [("ai_images", t("Yapay zeka her çekim için resim çizer + kamera hareketi", "AI image per shot + camera motion")),
                            ("puppet", t("Kukla: PNG karakterler + dudak senkronu (kendi çizimleriniz)", "Puppets: PNG characters + lip sync")),
                            ("local_images", t("Kendi görsellerim", "My own images")),
                            ("placeholder", t("Test (renkli kartlar, ücretsiz)", "Test (colored cards, free)"))], "ai_images")
    v["style"] = ask.text("visual_style", t("Görsel stil tarifi (İngilizce)", "Visual style prompt"), design.get("visual_style") or style)
    if v["mode"] in ("ai_images", "puppet"):
        v["provider"] = ask.choose("image_provider", t("Resim yapay zekası", "Image AI"),
                                   [("gemini", "Gemini (Google)"), ("openai", "OpenAI"), ("placeholder", t("Test kartları", "Test cards"))],
                                   "gemini")
        if v["provider"] == "gemini" and not os.environ.get("GEMINI_API_KEY"):
            k = ask.secret("gemini_key", "GEMINI_API_KEY", "GEMINI_API_KEY")
            if k:
                set_env_value("GEMINI_API_KEY", k)
        if v["provider"] == "openai" and not os.environ.get("OPENAI_API_KEY"):
            k = ask.secret("openai_key", "OPENAI_API_KEY", "OPENAI_API_KEY")
            if k:
                set_env_value("OPENAI_API_KEY", k)
        v["per"] = ask.choose("image_per", t("Kaç resim?", "How many images?"),
                              [("shot", t("Her çekime bir resim (daha canlı)", "One per shot (livelier)")),
                               ("scene", t("Her sahneye bir resim (daha ucuz)", "One per scene (cheaper)"))], "shot")

    print(t("\n=== 7/9 Seslendirme ===", "\n=== 7/9 Voices ==="))
    vp = ask.choose("voice_provider", t("Seslendirme servisi", "Voice service"),
                    [("elevenlabs", "ElevenLabs"), ("google", "Google Cloud TTS (Chirp 3 HD)"), ("gemini", "Gemini TTS"),
                     ("placeholder", t("Test sesi (ücretsiz, robotik)", "Test voice (free, robotic)"))], "elevenlabs")
    cfg["voice"]["provider"] = vp
    envk = {"elevenlabs": "ELEVENLABS_API_KEY", "google": "GOOGLE_TTS_API_KEY", "gemini": "GEMINI_API_KEY"}.get(vp)
    if envk and not os.environ.get(envk):
        k = ask.secret(f"{vp}_voice_key", envk, envk)
        if k:
            set_env_value(envk, k)
    voices = elevenlabs_voices() if vp == "elevenlabs" and os.environ.get("ELEVENLABS_API_KEY") and ask.interactive else []
    for i, c in enumerate(chars, 1):
        c["voice"]["id"] = pick_voice(ask, f"char{i}_voice", c["name"][ch["languages"][0]], c["voice"]["style"], vp, voices)
    if cfg["narrator"]["enabled"]:
        cfg["narrator"]["voice"]["id"] = pick_voice(ask, "narrator_voice", t("Anlatıcı", "Narrator"), cfg["narrator"]["voice"]["style"], vp, voices)

    print(t("\n=== 8/9 Zamanlama ===", "\n=== 8/9 Schedule ==="))
    s = cfg["schedule"]
    s["timezone"] = ask.text("timezone", t("Saat dilimi", "Time zone"), s["timezone"])
    pdays = ask.items("produce_days", t("Üretim günleri (mon,tue,wed,thu,fri,sat,sun; boş = otomatik üretim yok)",
                                        "Production days (mon…sun; empty = no automatic production)"), ["mon", "thu"])
    if pdays:
        s["produce"] = [dict(days=pdays, time=ask.text("produce_time", t("Üretim saati", "Production time"), "09:00"))]
    s["publish_times"] = ask.items("publish_times", t("Yayın saatleri (YouTube)", "Publish times (YouTube)"), ["17:00"])
    s["publish_days"] = ask.items("publish_days", t("Yayın günleri (boş = her gün)", "Publish days (empty = any day)"), [])

    print(t("\n=== 9/9 YouTube ve SEO ===", "\n=== 9/9 YouTube and SEO ==="))
    y = cfg["youtube"]
    y["enabled"] = ask.yes("youtube", t("Videolar YouTube'a otomatik yüklensin mi?", "Upload videos to YouTube automatically?"), True)
    if y["enabled"]:
        y["privacy"] = ask.choose("privacy", t("Yükleme görünürlüğü", "Upload visibility"),
                                  [("scheduled", t("Zamanlanmış yayın (yayın saatinde açılır)", "Scheduled (goes public at a publish slot)")),
                                   ("private", t("Özel (ben açarım)", "Private (I publish myself)")),
                                   ("unlisted", t("Liste dışı", "Unlisted")), ("public", t("Hemen herkese açık", "Public right away"))], "scheduled")
        y["upload_approval"] = ask.yes("upload_approval", t("Yüklemeden önce son videoyu onaylamamı beklesin mi?", "Wait for my approval before upload?"), True)
        y["category_id"] = ask.text("category_id", t("YouTube kategori no (1 Film/Animasyon, 24 Eğlence, 27 Eğitim)",
                                                     "YouTube category id (1 Film/Animation, 24 Entertainment, 27 Education)"), cat)
        y["playlist_id"] = ask.text("playlist_id", t("Oynatma listesi kimliği (isteğe bağlı)", "Playlist id (optional)"), "")
    cfg["seo"]["keywords"] = ask.items("keywords", t("Kanalın anahtar kelimeleri (virgülle)", "Channel keywords (comma separated)"), design.get("keywords", []))
    cfg["seo"]["hashtags"] = ask.items("hashtags", t("Sabit hashtag'ler", "Fixed hashtags"), [h.lstrip("#") for h in design.get("hashtags", [])][:3])
    music = ask.text("music_dir", t("Fon müziği klasörü (isteğe bağlı, telifsiz müzik)", "Background music folder (optional, royalty-free)"), "")
    cfg["audio"]["music_dir"] = music

    write_all(cfg, design)
    after_setup(cfg, ask)


def write_all(cfg, design: dict) -> None:
    d = channel_dir()
    for sub in ("icerik/senaryolar", "karakterler", "muzik"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    for c in cfg["characters"]:
        (d / "karakterler" / c["id"]).mkdir(parents=True, exist_ok=True)
    config.save(cfg)
    (d / "KANAL.md").write_text(bible(cfg, design), encoding="utf-8")
    ideas = d / "icerik" / "fikirler.md"
    if not ideas.exists():
        rows = [t("# Fikir kuyruğu", "# Idea queue"), "",
                t("Her satır bir bölüm fikri. Üretimde en üstteki kullanılır ve aşağı taşınır.", "One episode idea per line. The top one is used next."),
                ""] + [f"- {x}" for x in design.get("ideas", [])] + ["", t("## Kullanılanlar", "## Used"), ""]
        ideas.write_text("\n".join(rows) + "\n", encoding="utf-8")
    ex = d / "icerik" / "senaryolar" / "_ornek.md"
    if not ex.exists():
        src = ROOT / "ornekler" / ("senaryo-ornegi.tr.md" if cfg["ui_language"] == "tr" else "senaryo-ornegi.en.md")
        if src.exists():
            ex.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    env = ROOT / ".env"
    if not env.exists():
        env.write_text("# " + t("API anahtarları — bu dosyayı kimseyle paylaşmayın, git'e eklenmez.", "API keys — never share this file; git ignores it.") + "\n")


def bible(cfg, design: dict) -> str:
    """KANAL.md: the human-readable channel bible. The AI reads it before every script; edit it freely."""
    ch, v = cfg["channel"], cfg["visuals"]
    T = cfg["ui_language"] == "tr"
    L = (lambda tr, en: tr if T else en)
    rows = [f"# {ch['name']}", "",
            L("> Bu dosya kanalınızın kimliğidir. Yapay zeka her senaryodan önce okur; dilediğiniz gibi düzenleyin.",
              "> This is your channel bible. The AI reads it before every script; edit freely."), "",
            L("## Kanal kimliği", "## Identity"), "",
            f"- **{L('Konsept', 'Concept')}:** {ch['concept']}",
            f"- **{L('Tür', 'Type')}:** {t(*CHANNEL_TYPES[ch['type']])}",
            f"- **{L('Hedef kitle', 'Audience')}:** {ch['audience']}",
            f"- **{L('Ton', 'Tone')}:** {ch.get('tone', '')}",
            f"- **{L('Diller', 'Languages')}:** {', '.join(ch['languages'])} ({L('ilk dil ana dil', 'first is main')})",
            f"- **{L('Çocuklara özel', 'Made for kids')}:** {L('Evet', 'Yes') if ch['made_for_kids'] else L('Hayır', 'No')}", "",
            L("## Karakterler", "## Characters"), ""]
    if cfg["characters"]:
        rows += [L("| Kimlik | Ad | Görünüş | Ses |", "| Id | Name | Look | Voice |"), "| --- | --- | --- | --- |"]
        rows += [f"| `{c['id']}` | {' / '.join(dict.fromkeys(c['name'].values()))} | {c['description']} | {c['voice']['style']} |" for c in cfg["characters"]]
    else:
        rows.append(L("Tekrar eden karakter yok; anlatıcı ağırlıklı.", "No recurring characters; narrator-led."))
    if cfg["narrator"]["enabled"]:
        rows += ["", f"**{L('Anlatıcı', 'Narrator')}:** {cfg['narrator']['voice']['style']}"]
    rows += ["", L("## Görsel stil", "## Visual style"), "", v["style"], "",
             L("## Senaryo kuralları", "## Writing rules"), "", cfg["script"].get("extra_rules") or L(
                 "- (Buraya kendi kurallarınızı yazın: kaçınılacak konular, tekrar eden sahneler, açılış/kapanış cümleleri…)",
                 "- (Add your own rules: topics to avoid, recurring bits, opening/closing lines…)"), "",
             L("## Format ve takvim", "## Format and schedule"), "",
             f"- {L('Bölüm', 'Episode')}: ~{cfg['format']['target_minutes']} {L('dk', 'min')}, {cfg['format']['aspect']}; "
             f"Shorts: {cfg['format']['shorts_per_episode']} / {L('bölüm', 'episode')}",
             f"- {L('Üretim', 'Production')}: {cfg['schedule'].get('produce') or L('elle', 'manual')}",
             f"- {L('Yayın saatleri', 'Publish times')}: {', '.join(cfg['schedule']['publish_times'])} ({cfg['schedule']['timezone']})", "",
             L("## SEO", "## SEO"), "",
             f"- {L('Anahtar kelimeler', 'Keywords')}: {', '.join(cfg['seo']['keywords'])}",
             f"- Hashtag: {' '.join('#' + h for h in cfg['seo']['hashtags'])}", "",
             L("## Notlar", "## Notes"), "", ""]
    return "\n".join(rows)


def after_setup(cfg, ask: Asker) -> None:
    from . import puppet, visuals
    if cfg["visuals"]["mode"] == "puppet":
        cols = [(255, 152, 67), (90, 150, 255), (120, 200, 120), (230, 110, 160), (180, 130, 230)]
        for i, c in enumerate(cfg["characters"]):
            if not puppet.has_pack(c["id"]):
                puppet.build_example_pack(c["id"], cols[i % 5])
        print(t("Örnek kuklalar oluşturuldu (kanal/karakterler/<id>/kukla). Kendi çizimlerinizle değiştirin.",
                "Example puppets created (kanal/karakterler/<id>/kukla). Replace them with your own art."))
    if (cfg["visuals"]["mode"] == "ai_images" and cfg["visuals"]["provider"] in ("gemini", "openai") and cfg["characters"]
            and os.environ.get("GEMINI_API_KEY" if cfg["visuals"]["provider"] == "gemini" else "OPENAI_API_KEY")
            and ask.yes("make_references", t("Karakterlerin referans resmi şimdi çizilsin mi? (tutarlılık için önerilir)",
                                             "Draw character reference images now? (recommended for consistency)"), True)):
        for c in cfg["characters"]:
            try:
                print("  →", visuals.make_reference(cfg, c["id"]))
            except Exception as e:
                print(t(f"  {c['id']}: çizilemedi: {e}", f"  {c['id']}: failed: {e}"))
    print(t(f"""
Kurulum tamam.
  Kanal ayarı : kanal/kanal.yaml      Kanal kimliği: kanal/KANAL.md
  Fikirler    : kanal/icerik/fikirler.md   Kendi senaryolarınız: kanal/icerik/senaryolar/
Sonraki adımlar:
  python studio.py doktor         # eksik bir şey var mı?
  python studio.py yeni           # ilk bölümü üret
  python studio.py youtube-baglan # YouTube hesabını bağla (README'ye bakın)
  python studio.py zamanlayici --kur   # otomatik üretimi kur""", f"""
Setup complete.
  Config: kanal/kanal.yaml      Channel bible: kanal/KANAL.md
  Ideas : kanal/icerik/fikirler.md   Your scripts: kanal/icerik/senaryolar/
Next:
  python studio.py doctor            # anything missing?
  python studio.py new               # make the first episode
  python studio.py youtube-connect   # connect YouTube (see README)
  python studio.py scheduler --install   # set up automatic production"""))
