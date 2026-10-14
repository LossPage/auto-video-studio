"""Setup wizard: builds the channel step by step as a conversation and ends with a test video.

  1 Başlangıç / Start        interface + video languages, the AI helper and its key
  2 Konsept / Concept        the AI asks about your idea, drafts a concept → "Beğendin mi?" → revise until you like it
  3 Karakterler / Cast       you write detailed prompts, or the AI writes them from the concept; reference images
  4 Senaryo / Scripts        you give an example script, or the AI writes; a series plan and the first episode
  5 Seslendirme / Voices     voice service + key (or where to put it), one voice per speaker with a listening test
  6 Test videosu / Test      renders the first episode

Every step ends with a review: Enter accepts, anything else you type is treated as feedback and the step is redone.
Progress is saved to kanal/.kurulum.json after each step; running `kurulum` again continues where you left off.
Non-interactive: `python studio.py kurulum --cevaplar answers.yaml` (keys = question ids below; reviews auto-accept).
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from pathlib import Path

import yaml

from . import config, llm
from .config import CHANNEL_TYPES, DEFAULTS
from .util import ROOT, channel_dir, http_json, load_env, read_json, set_env_value, set_ui_lang, slugify, t, write_json

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
KEY_URLS = {
    "ANTHROPIC_API_KEY": "https://console.anthropic.com/settings/keys",
    "GEMINI_API_KEY": "https://aistudio.google.com/apikey",
    "OPENAI_API_KEY": "https://platform.openai.com/api-keys",
    "ELEVENLABS_API_KEY": "https://elevenlabs.io/app/settings/api-keys",
    "GOOGLE_TTS_API_KEY": "https://console.cloud.google.com/apis/credentials  (Cloud Text-to-Speech API)",
}
STEPS = [("start", "Başlangıç", "Getting started"), ("concept", "Kanal konsepti", "Channel concept"),
         ("cast", "Karakterler ve görseller", "Characters and visuals"), ("script", "Senaryo ve bölümler", "Scripts and episodes"),
         ("voice", "Seslendirme", "Voices"), ("test", "Test videosu", "Test video")]
YES = ("", "e", "evet", "y", "yes", "true", "ok", "tamam")
REPO = Path(__file__).resolve().parent.parent  # bundled examples live next to the code, not in STUDIO_ROOT


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

    def multiline(self, key, prompt) -> str:
        if key in self.a:
            return str(self.a[key])
        if not self.interactive:
            return ""
        print(f"\n{prompt}\n" + t("  (Bitirmek için boş bir satıra BITTI yazın.)", "  (Type DONE on an empty line to finish.)"))
        rows = []
        while True:
            ln = input()
            if ln.strip().upper() in ("BITTI", "BİTTİ", "DONE"):
                return "\n".join(rows).strip()
            rows.append(ln)


# ---------------------------------------------------------------- AI schemas
def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


S_STR, S_LIST = {"type": "string"}, {"type": "array", "items": {"type": "string"}}
QUESTIONS_SCHEMA = _obj({"questions": {"type": "array", "items": _obj({"question": S_STR, "example": S_STR})}})
CONCEPT_SCHEMA = _obj({"name_suggestions": S_LIST, "concept": S_STR, "audience": S_STR, "tone": S_STR,
                       "made_for_kids": {"type": "boolean"}, "target_minutes": {"type": "integer"}, "episode_formula": S_STR,
                       "writing_rules": S_LIST, "keywords": S_LIST, "hashtags": S_LIST})
CAST_SCHEMA = _obj({"visual_style": S_STR, "narrator_enabled": {"type": "boolean"}, "narrator_voice_style": S_STR,
                    "characters": {"type": "array", "items": _obj({"name": S_STR, "description": S_STR, "personality": S_STR,
                                                                  "voice_style": S_STR})}})
PLAN_SCHEMA = _obj({"episodes": {"type": "array", "items": _obj({"title": S_STR, "idea": S_STR})}})


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
        print(t(f"\n{who} için ses (tarif: {style}):", f"\nVoice for {who} (wanted: {style}):"))
        for i, v in enumerate(voices[:40], 1):
            print(f"  {i:2d}) {v['name']}  — {v['labels']}")
        v = input(t("Numara ya da voice_id (boş = sonra)", "Number or voice_id (empty = later)") + ": ").strip()
        if v.isdigit() and 1 <= int(v) <= len(voices[:40]):
            return voices[int(v) - 1]["id"]
        return v
    hint = GOOGLE_VOICES if provider in ("google", "gemini") else "voice_id (ElevenLabs > Voices)"
    return ask.text(key, t(f"{who} için ses (tarif: {style}). Seçenekler: {hint}", f"Voice for {who} ({style}). Options: {hint}"), "")


# ---------------------------------------------------------------- progress
def _progress_file() -> Path:
    return channel_dir() / ".kurulum.json"


class Setup:
    def __init__(self, ask: Asker, cfg: dict, design: dict):
        self.ask, self.design = ask, design
        self.cfg = config.Config(cfg)

    # ---------- helpers
    @property
    def ai_on(self) -> bool:
        return llm.available(self.cfg)

    def say(self, tr: str, en: str = None) -> None:
        print(t(tr, en if en is not None else tr))

    def review(self, key: str, show, revise) -> None:
        """show(); 'Beğendin mi?' → Enter accepts, other text is feedback for revise(feedback); repeat."""
        n = 0
        while True:
            show()
            v = str(self.ask.text(key if n == 0 else f"{key}_{n}",
                                  t("Beğendin mi? Enter = evet, devam · değiştirmek istediğin bir şey varsa yaz",
                                    "Do you like it? Enter = yes, continue · or type what to change"), "")).strip()
            if v.lower() in YES:
                return
            if v.lower() in ("h", "hayır", "hayir", "n", "no", "false"):
                v = input(t("Neyi değiştireyim? ", "What should I change? ")).strip() if self.ask.interactive else ""
                if not v:
                    continue
            try:
                revise(v)
            except Exception as e:
                self.say(f"  Olmadı: {e}", f"  That failed: {e}")
            n += 1

    def ai(self, system: str, prompt: str, schema: dict, max_tokens: int = 16000):
        """Structured AI call with a retry prompt. → dict, or None when the user gives up (manual fallback)."""
        while True:
            self.say("  … yapay zeka hazırlıyor", "  … the AI is working")
            try:
                return llm.generate(self.cfg, system, prompt, schema, max_tokens=max_tokens)
            except Exception as e:
                self.say(f"  Yapay zeka yanıt vermedi: {e}", f"  The AI call failed: {e}")
                if not (self.ask.interactive and self.ask.yes("_retry", t("Tekrar denensin mi?", "Try again?"), True)):
                    return None

    def ensure_key(self, env: str, purpose: str, answer_key: str) -> bool:
        """Ask for a key, or explain where to put it in .env. → key available"""
        load_env()
        if os.environ.get(env):
            self.say(f"  ✓ {env} bulundu (.env)", f"  ✓ {env} found (.env)")
            return True
        if answer_key in self.ask.a and self.ask.a[answer_key]:
            set_env_value(env, str(self.ask.a[answer_key]))
            return True
        if not self.ask.interactive:
            return False
        self.say(f"""
{purpose} için bir API anahtarı gerekiyor: {env}
  Nereden alınır: {KEY_URLS.get(env, '')}
  İki yol var:
   1) Anahtarı şimdi buraya yapıştır (ekranda görünmez), ya da
   2) Proje klasöründeki .env dosyasını aç, '{env}=' satırının sonuna anahtarı yaz, kaydet ve burada Enter'a bas.
  Boş bırakıp Enter'a basarsan bu adımı sonra yaparsın.""", f"""
{purpose} needs an API key: {env}
  Get one at: {KEY_URLS.get(env, '')}
  Either paste it here now (hidden), or open the .env file in the project folder, put it after '{env}=', save and press Enter.
  Leave it empty to add it later.""")
        import getpass
        v = getpass.getpass(f"{env}: ").strip()
        if v:
            set_env_value(env, v)
        else:
            os.environ.pop(env, None)
            load_env()
        ok = bool(os.environ.get(env))
        self.say(f"  ✓ {env} kaydedildi (.env, git'e eklenmez)" if ok else f"  – {env} yok; sonra .env dosyasına ekleyebilirsin.",
                 f"  ✓ {env} saved (.env, never committed)" if ok else f"  – no {env}; add it to .env later.")
        return ok

    def save(self, step: int) -> None:
        write_all(self.cfg, self.design)
        write_json(_progress_file(), dict(step=step, cfg=dict(self.cfg), design=self.design))

    # ---------- 1. start
    def start(self):
        ask, cfg = self.ask, self.cfg
        ch = cfg["channel"]
        ch["languages"] = ask.items("languages", t("Videolar hangi dillerde olsun? İlk yazdığın ana dil olur (ör. tr veya tr, en)",
                                                   "Which languages should videos be in? The first is the main one (e.g. en or en, es)"),
                                    [cfg["ui_language"]])
        if len(ch["languages"]) > 1:
            ch["multi_language"] = ask.choose("multi_language", t("Birden çok dil nasıl yayınlansın?", "How should several languages be published?"),
                                              [("multi_audio", t("Tek video, her dil ayrı ses kanalı (YouTube çok dilli ses)", "One video, an audio track per language")),
                                               ("separate_videos", t("Her dil ayrı video", "A separate video per language"))], "multi_audio")
        self.say("\nBu kurulumda yapay zeka sana sorular soracak, konsepti, karakterleri ve senaryoları yazacak.",
                 "\nDuring setup an AI will ask you questions and write the concept, characters and scripts.")
        lp = ask.choose("llm_provider", t("Hangi yapay zekayı kullanalım?", "Which AI should we use?"),
                        [("anthropic", "Claude (Anthropic)"), ("gemini", "Gemini (Google)"), ("openai", "OpenAI"),
                         ("none", t("Yapay zeka olmadan devam et (her şeyi ben yazarım)", "No AI (I write everything myself)"))], "anthropic")
        cfg["llm"]["provider"] = lp
        if lp == "none":
            return
        if lp == "gemini":
            cfg["google"]["endpoint"] = ask.choose("google_endpoint", t("Google anahtar türü", "Google key type"),
                                                   [("aistudio", "AI Studio (aistudio.google.com)"), ("vertex", "Vertex AI / Agent Platform")], "aistudio")
        cfg["llm"]["model"] = ask.text("llm_model", t("Model (Enter = önerilen)", "Model (Enter = recommended)"), llm.DEFAULT_MODELS[lp])
        while self.ensure_key(llm.KEY_NAMES[lp], t("Yapay zeka", "The AI"), f"{lp}_key") and ask.interactive:
            try:
                llm.generate(cfg, "Reply with the single word OK.", "Ping", None, max_tokens=2000)
                self.say("  ✓ Yapay zeka bağlantısı çalışıyor.", "  ✓ AI connection works.")
                break
            except Exception as e:
                self.say(f"  ✗ Anahtar denenemedi: {e}", f"  ✗ Key test failed: {e}")
                if not ask.yes("_rekey", t("Anahtarı yeniden girmek ister misin?", "Enter the key again?"), True):
                    break
                os.environ.pop(llm.KEY_NAMES[lp], None)
                set_env_value(llm.KEY_NAMES[lp], "")
                os.environ.pop(llm.KEY_NAMES[lp], None)
        if not self.ai_on:
            self.say("  Yapay zeka anahtarı olmadan devam ediyoruz; soruları sen cevaplayacaksın. Sonra .env'e ekleyebilirsin.",
                     "  Continuing without an AI key; you will fill things in yourself. Add the key to .env later.")

    # ---------- 2. concept
    def concept(self):
        ask, cfg, ch = self.ask, self.cfg, self.cfg["channel"]
        ch["type"] = ask.choose("type", t("Nasıl bir kanal?", "What kind of channel?"), [(k, t(*v)) for k, v in CHANNEL_TYPES.items()], ch["type"])
        mins, kids, cat, style = TYPE_DEFAULTS[ch["type"]]
        cfg["youtube"]["category_id"] = cat
        cfg["visuals"]["style"] = style
        if ch["type"] == "sleep":
            cfg["format"]["words_per_minute"] = 105
        seed = ask.text("concept", t("Kanalını kendi cümlelerinle anlat: ne anlatacak, kime, izleyici neden izlesin?",
                                     "Describe the channel in your own words: what, for whom, why would people watch?"), "")
        lang_ui = "Turkish" if cfg["ui_language"] == "tr" else "English"
        base = f"Channel type: {ch['type']}\nVideo languages: {', '.join(ch['languages'])}\nOwner's description: {seed or '(none)'}"
        answers = []
        if self.ai_on:
            q = self.ai("You are a YouTube channel strategist interviewing a new creator. Ask the few questions whose answers "
                        "would change the concept most (audience age, what makes it different, recurring format, tone, length, "
                        f"anything unclear in their description). Write questions and short example answers in {lang_ui}.",
                        base + "\n\nAsk 4-6 short questions.", QUESTIONS_SCHEMA, 4000)
            qs = (q or {}).get("questions", [])[:6]
            if qs:
                self.say("\nKonsepti netleştirmek için birkaç soru. Boş bırakırsan yapay zeka karar verir.",
                         "\nA few questions to sharpen the concept. Leave empty to let the AI decide.")
            for i, x in enumerate(qs, 1):
                a = ask.text(f"concept_q{i}", f"{i}) {x['question']}\n   ({t('örnek', 'e.g.')}: {x['example']})", "")
                answers.append(f"Q: {x['question']}\nA: {a or '(AI decides)'}")
        else:
            for key, q in [("audience", t("Hedef kitle kim? (ör. 4-7 yaş çocuklar)", "Who is the audience? (e.g. kids aged 4-7)")),
                           ("tone", t("Ton nasıl olsun? (ör. sıcak, eğlenceli, meraklı)", "What tone? (e.g. warm, funny, curious)")),
                           ("episode_formula", t("Her bölüm nasıl ilerler? (ör. bir soru sorulur, macerayla cevap bulunur)",
                                                 "How does every episode go? (e.g. a question is asked, an adventure answers it)"))]:
                self.design[key] = ask.text(key, q, self.design.get(key, ""))

        def draft(feedback: str = ""):
            prev = f"\n\nCurrent draft:\n{json.dumps(self.design.get('concept_draft'), ensure_ascii=False)}\n\nOwner's feedback: {feedback}" if feedback else ""
            d = self.ai("You design successful, original YouTube channels. Turn the owner's idea into a specific, production-ready "
                        "channel concept. name_suggestions: 3 short memorable names. episode_formula: what every episode looks like. "
                        "writing_rules: 4-8 concrete rules for the script writer. keywords: 10-15 search phrases in the video "
                        "languages. hashtags: 3 without '#'. target_minutes: a sensible episode length. "
                        f"Write all text in {lang_ui} except keywords (video languages).",
                        base + "\n\n" + "\n".join(answers) + prev, CONCEPT_SCHEMA)
            if d:
                self.design["concept_draft"] = d

        def show():
            d = self.design.get("concept_draft") or {}
            if not d:
                return
            print("\n" + "─" * 60)
            print(t("Ad önerileri: ", "Name ideas: ") + " · ".join(d["name_suggestions"]))
            print(t("Konsept: ", "Concept: ") + d["concept"])
            print(t("Kitle: ", "Audience: ") + d["audience"] + "  |  " + t("Ton: ", "Tone: ") + d["tone"])
            print(t("Bölüm süresi: ", "Episode length: ") + f"~{d['target_minutes']} " + t("dk", "min") + "  |  " +
                  t("Çocuklara özel: ", "Made for kids: ") + (t("Evet", "Yes") if d["made_for_kids"] else t("Hayır", "No")))
            print(t("Her bölüm: ", "Every episode: ") + d["episode_formula"])
            print(t("Yazım kuralları:", "Writing rules:"))
            for r in d["writing_rules"]:
                print(f"  - {r}")
            print(t("Anahtar kelimeler: ", "Keywords: ") + ", ".join(d["keywords"]))
            print("─" * 60)

        if self.ai_on:
            draft()
            if self.design.get("concept_draft"):
                self.review("concept_ok", show, draft)
        d = self.design.get("concept_draft") or {}
        names = d.get("name_suggestions") or []
        if names and ask.interactive and "name" not in ask.a:
            print(t("\nKanal adı ne olsun?", "\nChannel name?"))
            for i, n in enumerate(names, 1):
                print(f"  {i}) {n}")
            v = input(t("Numara ya da kendi bulduğun ad [1]: ", "Number or your own name [1]: ")).strip()
            ch["name"] = names[int(v) - 1] if v.isdigit() and 1 <= int(v) <= len(names) else (v or names[0])
        else:
            ch["name"] = ask.text("name", t("Kanal adı", "Channel name"), names[0] if names else ch["name"])
        ch["concept"] = d.get("concept") or seed
        ch["audience"] = ask.a.get("audience") or d.get("audience") or self.design.get("audience", "")
        ch["tone"] = d.get("tone") or self.design.get("tone", "")
        self.design["episode_formula"] = d.get("episode_formula") or self.design.get("episode_formula", "")
        cfg["script"]["extra_rules"] = "\n".join(f"- {r}" for r in d.get("writing_rules", []))
        cfg["format"]["target_minutes"] = ask.number("target_minutes", t("Bölüm süresi (dakika)", "Episode length (minutes)"),
                                                     d.get("target_minutes") or mins)
        cfg["format"]["shorts_per_episode"] = ask.number("shorts_per_episode", t("Her bölümden kaç Shorts çıkarılsın?", "Shorts per episode?"), 2)
        if "aspect" in ask.a:
            cfg["format"]["aspect"] = ask.a["aspect"]
        ch["made_for_kids"] = ask.yes("made_for_kids", t("Çocuklara özel mi? (YouTube kuralı: yorumlar ve kişisel reklam kapanır)",
                                                         "Made for kids? (YouTube rule: comments and personalized ads turn off)"),
                                      d.get("made_for_kids", kids))
        cfg["seo"]["keywords"] = ask.items("keywords", t("Anahtar kelimeler", "Keywords"), d.get("keywords", [])) if not d else d["keywords"]
        cfg["seo"]["hashtags"] = ask.items("hashtags", "Hashtag", [h.lstrip("#") for h in d.get("hashtags", [])][:3]) if not d else \
            [h.lstrip("#") for h in d["hashtags"]][:3]

    # ---------- 3. characters and visuals
    def cast(self):
        ask, cfg = self.ask, self.cfg
        how = ask.choose("cast_mode", t("Karakterler ve görsel stil nasıl belirlensin?", "How should characters and the visual style be made?"),
                         ([("ai", t("Yapay zeka konsepte göre yazsın, ben beğenip onaylayayım", "The AI writes them from the concept, I approve"))]
                          if self.ai_on else []) +
                         [("me", t("Ben detaylı tarif (prompt) yazacağım", "I write detailed descriptions (prompts)")),
                          ("none", t("Karakter yok, yalnız anlatıcı", "No characters, narrator only"))],
                         "ai" if self.ai_on else ("me" if "character_count" in ask.a or ask.interactive else "none"))
        if how == "ai":
            self._cast_ai()
        elif how == "me":
            self._cast_manual()
            self.review("cast_ok", self._show_cast, lambda fb: self._cast_manual())
        else:
            cfg["characters"] = []
            cfg["narrator"]["enabled"] = True
            cfg["visuals"]["style"] = ask.text("visual_style", t("Görsel stil tarifi (ör. 'soft watercolor, pastel colors, cozy')",
                                                                 "Visual style prompt"), cfg["visuals"]["style"])
        self._visual_mode()
        self.save(2)    # characters must be on disk before reference images (step stays open until they are approved)
        self._references()

    def _cast_ai(self):
        cfg = self.cfg

        def make(feedback: str = ""):
            prev = (f"\n\nCurrent cast:\n{json.dumps(self.design.get('cast'), ensure_ascii=False)}\n\nOwner's feedback: {feedback}"
                    if feedback else "")
            d = self.ai("You are the art director and casting director of a YouTube channel. Design 0-5 recurring characters that "
                        "fit the concept (none if the format does not need them) and one visual style. Each description is a "
                        "precise English image prompt an image model can reproduce every time: species/age, body shape, colors "
                        "(name them), clothing, one signature prop. personality: 1 sentence in the owner's language. voice_style: "
                        "English TTS voice brief (age, gender, energy, pace). visual_style: one reusable English image-style prompt.",
                        f"Channel bible:\n{config.Config(cfg).bible() or cfg['channel']['concept']}{prev}", CAST_SCHEMA)
            if d:
                self.design["cast"] = d
                self._apply_cast(d)

        make()
        if not self.design.get("cast"):
            return self._cast_manual()
        self.review("cast_ok", self._show_cast, make)

    def _apply_cast(self, d: dict):
        cfg = self.cfg
        langs = cfg["channel"]["languages"]
        cfg["characters"] = []
        for i, c in enumerate(d["characters"], 1):
            cid = slugify(c["name"], 20).replace("-", "_") or f"char{i}"
            if cid == "narrator" or any(x["id"] == cid for x in cfg["characters"]):
                cid = f"{cid}_{i}"
            cfg["characters"].append(dict(id=cid, name={lg: c["name"] for lg in langs}, description=c["description"],
                                          personality=c.get("personality", ""), voice=dict(id="", style=c["voice_style"]), speed=1.0))
        cfg["narrator"]["enabled"] = bool(d["narrator_enabled"]) or not cfg["characters"]
        cfg["narrator"]["voice"]["style"] = d["narrator_voice_style"] or cfg["narrator"]["voice"]["style"]
        cfg["visuals"]["style"] = d["visual_style"]

    def _cast_manual(self):
        ask, cfg = self.ask, self.cfg
        langs = cfg["channel"]["languages"]
        self.say("\nHer karakter için görünüşünü bir resim istemi gibi detaylı yaz: tür/yaş, renkler, kıyafet, aksesuar.\n"
                 "  Örnek: 'small blue bunny, long floppy ears, light-blue belly, big round eyes, yellow scarf'\n"
                 "  İngilizce yazarsan resim modelleri daha tutarlı çizer.",
                 "\nDescribe each character's look like an image prompt: species/age, colors, clothing, props.\n"
                 "  Example: 'small blue bunny, long floppy ears, light-blue belly, big round eyes, yellow scarf'")
        n = ask.number("character_count", t("Kaç tekrar eden karakter olacak? (0 = yalnız anlatıcı)", "How many recurring characters? (0 = narrator only)"),
                       len(cfg["characters"]) or 2)
        chars = []
        for i in range(int(n)):
            old = cfg["characters"][i] if i < len(cfg["characters"]) else {}
            name = ask.text(f"char{i + 1}_name", t(f"{i + 1}. karakterin adı", f"Character {i + 1} name"),
                            next(iter((old.get("name") or {}).values()), ""))
            if not name:
                continue
            desc = ask.text(f"char{i + 1}_description", t(f"{name}: görünüş (detaylı istem)", f"{name}: look (detailed prompt)"), old.get("description", ""))
            pers = ask.text(f"char{i + 1}_personality", t(f"{name}: kişiliği (1 cümle)", f"{name}: personality (1 sentence)"), old.get("personality", ""))
            vstyle = ask.text(f"char{i + 1}_voice_style", t(f"{name}: ses tarifi (yaş, cinsiyet, enerji)", f"{name}: voice (age, gender, energy)"),
                              (old.get("voice") or {}).get("style", ""))
            cid = slugify(name, 20).replace("-", "_") or f"char{i + 1}"
            if cid == "narrator" or any(x["id"] == cid for x in chars):
                cid = f"{cid}_{i + 1}"
            chars.append(dict(id=cid, name={lg: name for lg in langs}, description=desc, personality=pers,
                              voice=dict(id="", style=vstyle), speed=1.0))
        cfg["characters"] = chars
        cfg["narrator"]["enabled"] = ask.yes("narrator", t("Anlatıcı (dış ses) olsun mu?", "Use a narrator voice?"), True) or not chars
        cfg["visuals"]["style"] = ask.text("visual_style", t("Görsel stil istemi (ör. 'bright 2D cartoon, soft shading, rounded shapes')",
                                                             "Visual style prompt (e.g. 'bright 2D cartoon, soft shading')"), cfg["visuals"]["style"])

    def _show_cast(self):
        cfg = self.cfg
        print("\n" + "─" * 60)
        print(t("Görsel stil: ", "Visual style: ") + cfg["visuals"]["style"])
        for c in cfg["characters"]:
            print(f"\n• {next(iter(c['name'].values()))}  ({c['id']})")
            print(t("  Görünüş: ", "  Look: ") + c["description"])
            if c.get("personality"):
                print(t("  Kişilik: ", "  Personality: ") + c["personality"])
            print(t("  Ses: ", "  Voice: ") + c["voice"]["style"])
        if cfg["narrator"]["enabled"]:
            print("\n• " + t("Anlatıcı: ", "Narrator: ") + cfg["narrator"]["voice"]["style"])
        print("─" * 60)

    def _visual_mode(self):
        ask, v = self.ask, self.cfg["visuals"]
        v["mode"] = ask.choose("visual_mode", t("Videodaki görüntüler nasıl üretilsin?", "How should the pictures be made?"),
                               [("ai_images", t("Yapay zeka her çekim için resim çizsin + kamera hareketi", "AI image per shot + camera motion")),
                                ("puppet", t("Kukla: PNG karakterler + dudak senkronu (kendi çizimlerim)", "Puppets: PNG characters + lip sync (my art)")),
                                ("local_images", t("Kendi resimlerimi koyacağım", "I will add my own images")),
                                ("placeholder", t("Şimdilik test kartları (ücretsiz)", "Test cards for now (free)"))], v["mode"])
        if v["mode"] in ("ai_images", "puppet"):
            v["provider"] = ask.choose("image_provider", t("Resimleri hangi yapay zeka çizsin?", "Which AI draws the images?"),
                                       [("gemini", "Gemini (Google)"), ("openai", "OpenAI"), ("placeholder", t("Test kartları", "Test cards"))],
                                       v["provider"] if v["provider"] in ("gemini", "openai") else "gemini")
            env = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}.get(v["provider"])
            if env:
                self.ensure_key(env, t("Resim çizimi", "Image generation"), f"{v['provider']}_image_key")
                v["per"] = ask.choose("image_per", t("Kaç resim çizilsin?", "How many images?"),
                                      [("shot", t("Her çekime bir resim (daha canlı, ~40-60 resim / 10 dk)", "One per shot (livelier)")),
                                       ("scene", t("Her sahneye bir resim (daha ucuz)", "One per scene (cheaper)"))], v["per"])

    def _references(self):
        from . import puppet, visuals
        cfg, ask = self.cfg, self.ask
        if cfg["visuals"]["mode"] == "puppet":
            cols = [(255, 152, 67), (90, 150, 255), (120, 200, 120), (230, 110, 160), (180, 130, 230)]
            for i, c in enumerate(cfg["characters"]):
                if not puppet.has_pack(c["id"]):
                    puppet.build_example_pack(c["id"], cols[i % 5])
            self.say("  Örnek kuklalar oluşturuldu: kanal/karakterler/<id>/kukla/ (kendi çizimlerinle değiştir, README > Görüntü).",
                     "  Example puppets created: kanal/karakterler/<id>/kukla/ (replace with your art, see README).")
            return
        env = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}.get(cfg["visuals"]["provider"])
        if cfg["visuals"]["mode"] != "ai_images" or not env or not os.environ.get(env) or not cfg["characters"]:
            return
        if not ask.yes("make_references", t("Karakterlerin referans resmini şimdi çizelim mi? Her videoda karakterler bu resme bakılarak çizilir.",
                                            "Draw a reference image of each character now? Every video uses it for consistency."), True):
            return
        for c in cfg["characters"]:
            name = next(iter(c["name"].values()))

            def draw(feedback: str = "", c=c):
                if feedback:
                    c["description"] = f"{c['description']}. {feedback}"
                    config.save(cfg)
                print("  →", visuals.make_reference(config.Config(cfg), c["id"]))

            try:
                draw()
            except Exception as e:
                self.say(f"  {name}: çizilemedi: {e}", f"  {name}: failed: {e}")
                continue
            self.say(f"  {name} hazır. Resmi aç ve bak. Değişiklik istersen yaz (ör. 'kulakları daha uzun, gözler yeşil').",
                     f"  {name} is ready. Open the image. Type changes if you want any.")
            self.review(f"ref_{c['id']}_ok", lambda: None, draw)

    # ---------- 4. scripts and episodes
    def script(self):
        from . import script as S
        ask, cfg = self.ask, self.cfg
        langs = cfg["channel"]["languages"]
        if len(langs) > 1:
            cfg["script"]["written_language"] = ask.text("written_language", t("Senaryoları (ya da örneğini) hangi dilde yazarsın?",
                                                                               "Which language do you write scripts in?"),
                                                         cfg["ui_language"] if cfg["ui_language"] in langs else langs[0])
        src = ask.choose("script_source", t("Senaryo için bir örneğin var mı?", "Do you have an example script?"),
                         [("example", t("Evet, örnek senaryo vereceğim (dosya ya da yapıştır)", "Yes, I'll give an example (file or paste)")),
                          ("ai", t("Hayır, sen yaz (yapay zeka yazsın)", "No, you write it (the AI writes)"))] if self.ai_on else
                         [("example", t("Örnek senaryo vereceğim", "I'll give an example script")),
                          ("builtin", t("Hazır örnekle başla (ornekler/senaryo-ornegi)", "Start with the bundled example"))],
                         "ai" if self.ai_on else "builtin")
        first, source = None, None
        if src == "example":
            text = self._read_example()
            if text:
                first, source = self._use_example(text)
        elif src == "builtin":
            p = REPO / "ornekler" / ("senaryo-ornegi.tr.md" if cfg["script"].get("written_language", cfg["ui_language"]) == "tr" else "senaryo-ornegi.en.md")
            first, source = self._use_example(p.read_text(encoding="utf-8"), name=p.name)
        modes = [("idea", t("Bölüm planından sırayla, yapay zeka yazar (fikir ekleyebilirim: kanal/icerik/fikirler.md)",
                            "From the episode plan in order, the AI writes (I can add ideas: kanal/icerik/fikirler.md)")),
                 ("ai", t("Plan bitince konuyu da yapay zeka seçsin", "When the plan runs out, the AI picks topics too")),
                 ("manual", t("Senaryoları ben yazarım (kanal/icerik/senaryolar/)", "I write the scripts (kanal/icerik/senaryolar/)"))]
        cfg["script"]["mode"] = ask.choose("script_mode", t("Sonraki bölümlerin senaryoları nasıl oluşsun?", "How should later scripts be made?"),
                                           modes if self.ai_on else modes[2:], "idea" if self.ai_on else "manual")
        if cfg["script"]["mode"] == "ai":     # plan first, then free AI topics
            cfg["script"]["mode"], cfg["script"]["fallback_to_ai"] = "idea", True
        if self.ai_on and cfg["script"]["mode"] != "manual":
            self._plan()
        self.save(3)
        if first is None and self.ai_on:
            ideas = S.pending_ideas()
            idea = ideas[0] if ideas else None
            self.say("\nİlk bölümü yazıyorum…", "\nWriting the first episode…")
            try:
                first, source = S.generate_ai(self.cfg, idea), (dict(mode="idea", idea=idea) if idea else dict(mode="ai"))
            except Exception as e:
                self.say(f"  İlk bölüm yazılamadı: {e}", f"  First episode failed: {e}")
        if first is not None:
            draft = channel_dir() / "icerik" / "ilk-bolum.md"

            def show():
                st = S.stats(first, self.cfg)
                P = self.cfg.primary
                draft.write_text(S.to_markdown(first, self.cfg), encoding="utf-8")
                print("\n" + "─" * 60)
                print(t("İlk bölüm: ", "First episode: ") + first["title"][P])
                print(first["summary"][P])
                print(t(f"{st['scenes']} sahne · {st['lines']} replik · ~{st['est_minutes']} dk", f"{st['scenes']} scenes · {st['lines']} lines · ~{st['est_minutes']} min"))
                print(t(f"Tam metin: {draft}", f"Full text: {draft}"))
                print("─" * 60)

            def revise(fb: str):
                nonlocal first
                first = S.revise(self.cfg, first, fb)

            self.review("first_script_ok", show, revise if self.ai_on else (lambda fb: None))
            self.design["first_script"], self.design["first_source"] = first, source
        cfg["script"]["approval"] = ask.yes("script_approval", t("Sonraki bölümlerde senaryo, görsellere geçmeden önce onayımı beklesin mi?",
                                                                 "For later episodes, wait for my approval of each script?"), True)

    def _read_example(self) -> str:
        ask = self.ask
        v = str(ask.text("example_script", t("Örnek senaryo dosyasının yolu (boş bırakırsan buraya yapıştırırsın)",
                                             "Path to the example script (empty = paste it here)"), "")).strip().strip('"')
        if v:
            p = Path(v).expanduser()
            if not p.is_absolute():
                p = next((b / p for b in (Path.cwd(), ROOT, REPO) if (b / p).exists()), p)
            if p.exists():
                return p.read_text(encoding="utf-8")
            self.say(f"  Dosya bulunamadı: {p}", f"  File not found: {p}")
        return ask.multiline("example_text", t("Örnek senaryoyu yapıştır:", "Paste the example script:"))

    def _use_example(self, text: str, name: str = "ornek.md"):
        """A script in the studio format becomes episode 1; any other text becomes a style reference for the AI."""
        from . import script as S
        cfg = config.Config(copy.deepcopy(dict(self.cfg)))
        try:
            parsed = S.parse_markdown(text, cfg)
            ok = any(l["speaker"] != "narrator" for sc in parsed["scenes"] for sh in sc["shots"] for l in sh["lines"]) or \
                len(parsed["scenes"]) > 1
        except ValueError:
            ok = False
        if ok:
            fn = name if name.endswith(".md") and name != "ornek.md" else f"{slugify(parsed['title'].get(parsed['_written'], 'ornek'), 40) or 'ornek'}.md"
            (S.manual_dir() / fn).parent.mkdir(parents=True, exist_ok=True)
            (S.manual_dir() / fn).write_text(text, encoding="utf-8")
            self.say(f"  Senaryo biçimini tanıdım; ilk bölüm bu olacak ({S.manual_dir() / fn}).",
                     f"  Recognized the script format; it becomes episode 1 ({S.manual_dir() / fn}).")
            sc = S.complete_manual(cfg, parsed)
            self.design["example_style"] = text[:6000]
            return sc, dict(mode="manual", file=fn)
        self.design["example_style"] = text[:6000]
        self.say("  Örneği üslup referansı olarak KANAL.md'ye ekledim; yapay zeka bu tarzda yazacak.",
                 "  Added the example to KANAL.md as a style reference; the AI will write in this style.")
        return None, None

    def _plan(self):
        from . import script as S
        ask, cfg = self.ask, self.cfg
        n = int(ask.number("plan_count", t("İlk planda kaç bölüm olsun?", "How many episodes in the first plan?"), 10))
        lang_ui = "Turkish" if cfg["ui_language"] == "tr" else "English"

        def make(feedback: str = ""):
            prev = (f"\n\nCurrent plan:\n{json.dumps(self.design.get('plan'), ensure_ascii=False)}\n\nOwner's feedback: {feedback}" if feedback else "")
            d = self.ai(f"You plan the first season of a YouTube channel. Propose {n} distinct, high-potential episodes that fit the channel "
                        "bible, ordered so the strongest come first. title: catchy, searchable. idea: 1-2 sentences with the hook and "
                        f"the payoff. Write in {lang_ui}.",
                        f"Channel bible:\n{config.Config(cfg).bible()}\n\nEarlier episodes: {S._history_block(S.history(config.Config(cfg)))}{prev}",
                        PLAN_SCHEMA)
            if d:
                self.design["plan"] = d["episodes"]

        def show():
            print("\n" + "─" * 60)
            for i, e in enumerate(self.design.get("plan") or [], 1):
                print(f"{i:2d}. {e['title']}\n    {e['idea']}")
            print("─" * 60)

        make()
        if not self.design.get("plan"):
            return
        self.review("plan_ok", show, make)
        p = S.ideas_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        rows = [t("# Bölüm planı", "# Episode plan"), "",
                t("Her satır bir bölüm. Üretimde en üstteki kullanılır ve aşağı taşınır. Dilediğin gibi ekle, sil, sırala.",
                  "One episode per line. The top one is used next. Add, remove or reorder freely."), ""]
        rows += [f"- {e['title']}: {e['idea']}" for e in self.design["plan"]] + ["", t("## Kullanılanlar", "## Used"), ""]
        p.write_text("\n".join(rows) + "\n", encoding="utf-8")
        self.say(f"  Plan kaydedildi: {p}", f"  Plan saved: {p}")

    # ---------- 5. voices
    def voice(self):
        ask, cfg = self.ask, self.cfg
        vp = ask.choose("voice_provider", t("Seslendirmeyi hangi servis yapsın?", "Which service should do the voices?"),
                        [("elevenlabs", t("ElevenLabs (en doğal, duygulu)", "ElevenLabs (most natural)")),
                         ("google", "Google Cloud TTS (Chirp 3 HD)"), ("gemini", "Gemini TTS"),
                         ("placeholder", t("Test sesi (ücretsiz, robotik)", "Test voice (free, robotic)"))], cfg["voice"]["provider"])
        cfg["voice"]["provider"] = vp
        env = {"elevenlabs": "ELEVENLABS_API_KEY", "google": "GOOGLE_TTS_API_KEY", "gemini": "GEMINI_API_KEY"}.get(vp)
        has_key = self.ensure_key(env, t("Seslendirme", "Voices"), f"{vp}_voice_key") if env else True
        self.design["test_voice_placeholder"] = not has_key
        if not has_key:
            self.say("  Anahtar olmadan test videosu ücretsiz test sesiyle yapılır. Anahtarı ekleyince: python studio.py doktor",
                     "  Without a key the test video uses the free test voice. After adding the key: python studio.py doctor")
        voices = elevenlabs_voices() if vp == "elevenlabs" and has_key and ask.interactive else []
        speakers = [(f"char{i}_voice", c["id"], next(iter(c["name"].values())), c["voice"]) for i, c in enumerate(cfg["characters"], 1)]
        if cfg["narrator"]["enabled"]:
            speakers.append(("narrator_voice", "narrator", t("Anlatıcı", "Narrator"), cfg["narrator"]["voice"]))
        for key, sid, who, voice in speakers:
            voice["id"] = voice.get("id") or pick_voice(ask, key, who, voice["style"], vp, voices)
            if has_key and voice["id"] and vp != "placeholder" and ask.interactive:
                self.review(f"{key}_ok", lambda sid=sid, who=who: self._preview(sid, who),
                            lambda fb, key=key, who=who, voice=voice: voice.update(id=pick_voice(ask, key, who, voice["style"], vp, voices) or voice["id"]))

    def _preview(self, sid: str, who: str):
        from . import tts
        cfg = config.Config(self.cfg)
        P = cfg.primary
        line = next((l for sc in (self.design.get("first_script") or {}).get("scenes", []) for sh in sc["shots"]
                     for l in sh["lines"] if l["speaker"] == sid), None)
        text = line["text"][P] if line else t(f"Merhaba! Ben {who}. Bugün harika bir maceraya çıkıyoruz.",
                                              f"Hello! I'm {who}. Today we're going on a wonderful adventure.")
        try:
            r = tts.synth(cfg, sid, text, line["emotion"] if line else "cheerful", P)
            d = channel_dir() / "ses-ornekleri"
            d.mkdir(exist_ok=True)
            out = d / f"{sid}.wav"
            shutil.copy(r["wav"], out)
            self.say(f"  {who} örneği: {out}  (aç ve dinle)  \"{text[:80]}\"", f"  {who} sample: {out}  (open and listen)")
        except Exception as e:
            self.say(f"  Örnek ses üretilemedi: {e}", f"  Could not make a sample: {e}")

    # ---------- 6. test video
    def test(self):
        from . import pipeline, script as S
        ask = self.ask
        sc = self.design.get("first_script")
        if not sc:
            self.say("Test videosu için senaryo yok; 'python studio.py yeni' ile ilk bölümü üretebilirsin.",
                     "No script for a test video; run 'python studio.py new' to make the first episode.")
            return
        if not ask.yes("test_video", t("İlk bölümden şimdi bir test videosu üretelim mi?", "Make a test video from the first episode now?"), True):
            return
        tcfg = config.Config(copy.deepcopy(dict(self.cfg)))
        full = ask.choose("test_length", t("Test videosu ne kadar olsun?", "How long should the test video be?"),
                          [("short", t("Kısa: ilk 2 sahne (daha hızlı ve ucuz)", "Short: first 2 scenes (faster, cheaper)")),
                           ("full", t("Tam bölüm", "The full episode"))], "short") == "full"
        sc = copy.deepcopy(sc)
        if not full:
            sc["scenes"] = sc["scenes"][:2]
            sc = S.normalize(sc, tcfg)
        if self.design.get("test_voice_placeholder"):
            tcfg["voice"]["provider"] = "placeholder"
        st = S.stats(sc, tcfg)
        imgs = st["shots"] if tcfg["visuals"]["per"] == "shot" else st["scenes"]
        chars = sum(len(l["text"][lg]) for x in sc["scenes"] for sh in x["shots"] for l in sh["lines"] for lg in tcfg.langs)
        self.say(f"\nTahmini: ~{st['est_minutes']} dk video, {chars} karakter seslendirme" +
                 (f", {imgs} yapay zeka resmi" if tcfg["visuals"]["mode"] == "ai_images" and tcfg["visuals"]["provider"] != "placeholder" else ""),
                 f"\nEstimate: ~{st['est_minutes']} min video, {chars} characters of speech" +
                 (f", {imgs} AI images" if tcfg["visuals"]["mode"] == "ai_images" and tcfg["visuals"]["provider"] != "placeholder" else ""))
        source = dict(self.design.get("first_source") or dict(mode="ai")) if full else dict(mode="test")  # a short test keeps the idea queued
        # A full test episode waits at the upload gate, so it can be published later with `onayla`; a short one stops at done.
        tcfg["youtube"]["enabled"], tcfg["youtube"]["upload_approval"] = full, True
        eid = pipeline.create_from_script(tcfg, sc, source, approved=True)
        self.say("Video üretiliyor; bu birkaç dakika sürebilir…", "Rendering; this can take a few minutes…")
        stage = pipeline.advance(tcfg, eid)
        d = pipeline.ep_dir(eid)
        if pipeline.state(eid).get("error"):
            self.say(f"\n✗ Test videosu tamamlanamadı ({stage}): {pipeline.state(eid)['error']}\n  Düzeltip: python studio.py uret {eid}",
                     f"\n✗ The test video stopped at {stage}: {pipeline.state(eid)['error']}\n  Fix and run: python studio.py run {eid}")
            return
        self.design["test_episode"] = eid
        self.say(f"\n✓ Test videosu hazır: {d / 'video.mp4'}\n  Klasör: {d}  (Shorts, altyazı, kapak, yayin.md)",
                 f"\n✓ Test video ready: {d / 'video.mp4'}\n  Folder: {d}  (Shorts, subtitles, thumbnail, yayin.md)")


# ---------------------------------------------------------------- main
def run(answers_file: str | None = None) -> None:
    answers = yaml.safe_load(Path(answers_file).read_text(encoding="utf-8")) if answers_file else None
    ask = Asker(answers)
    prog = read_json(_progress_file(), None) if _progress_file().exists() else None
    start = 0
    cfg, design = copy.deepcopy(DEFAULTS), {}
    if prog and ask.interactive:
        set_ui_lang(prog["cfg"].get("ui_language", "tr"))
        name = STEPS[min(prog["step"], len(STEPS) - 1)]
        if ask.yes("resume", t(f"Yarım kalmış bir kurulum var. Kaldığın yerden ({prog['step'] + 1}. adım: {name[1]}) devam edelim mi?",
                               f"There is an unfinished setup. Continue at step {prog['step'] + 1} ({name[2]})?"), True):
            start, cfg, design = prog["step"], config._merge(DEFAULTS, prog["cfg"]), prog["design"]
    elif config.path().exists() and ask.interactive and not ask.yes(
            "overwrite", t("Bu klasörde kurulu bir kanal var. Baştan kurulsun mu? (mevcut ayarların üzerine yazılır)",
                           "A channel is already set up here. Start over? (overwrites the current settings)"), False):
        return
    if start == 0:
        cfg["ui_language"] = ask.choose("ui_language", "Arayüz dili / Interface language", [("tr", "Türkçe"), ("en", "English")], "tr")
    set_ui_lang(cfg["ui_language"])
    if not (ROOT / ".env").exists() and (REPO / ".env.example").exists():
        shutil.copy(REPO / ".env.example", ROOT / ".env")     # so the user can see where each key goes
    load_env()
    s = Setup(ask, cfg, design)
    if start == 0:
        s.say("\nMerhaba! Kanalını birlikte, adım adım kuracağız. Her adımın sonunda beğenip beğenmediğini soracağım;\n"
              "beğenmezsen ne değişsin yazman yeterli. İstediğin an Ctrl+C ile çıkabilirsin, kaldığın yerden devam edersin.",
              "\nHi! We'll set up your channel together, step by step. After each step I'll ask if you like it;\n"
              "if not, just type what to change. Press Ctrl+C any time; you can continue later.")
    for i, (key, tr, en) in enumerate(STEPS):
        if i < start:
            continue
        print(f"\n{'━' * 60}\n  {t('Adım', 'Step')} {i + 1}/{len(STEPS)} · {t(tr, en)}\n{'━' * 60}")
        getattr(s, key)()
        s.save(i + 1)
    _advanced(s.cfg, ask.a)
    write_all(s.cfg, s.design)
    _progress_file().unlink(missing_ok=True)
    finish(s.cfg, s.design)


def _advanced(cfg, a: dict) -> None:
    """Schedule / YouTube / music are not asked (do them after the test); an answers file may still set them."""
    s, y = cfg["schedule"], cfg["youtube"]
    if "timezone" in a:
        s["timezone"] = a["timezone"]
    if a.get("produce_days"):
        s["produce"] = [dict(days=a["produce_days"], time=a.get("produce_time", "09:00"))]
    for k in ("publish_times", "publish_days"):
        if k in a:
            s[k] = a[k]
    for k, yk in (("youtube", "enabled"), ("privacy", "privacy"), ("upload_approval", "upload_approval"),
                  ("category_id", "category_id"), ("playlist_id", "playlist_id")):
        if k in a:
            y[yk] = a[k]
    if "music_dir" in a:
        cfg["audio"]["music_dir"] = a["music_dir"]


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
        rows = [t("# Bölüm planı", "# Episode plan"), "",
                t("Her satır bir bölüm fikri. Üretimde en üstteki kullanılır ve aşağı taşınır.", "One episode idea per line. The top one is used next."),
                "", "", t("## Kullanılanlar", "## Used"), ""]
        ideas.write_text("\n".join(rows) + "\n", encoding="utf-8")
    ex = d / "icerik" / "senaryolar" / "_ornek.md"
    if not ex.exists():
        src = REPO / "ornekler" / ("senaryo-ornegi.tr.md" if cfg["ui_language"] == "tr" else "senaryo-ornegi.en.md")
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
            f"- **{L('Her bölüm', 'Every episode')}:** {design.get('episode_formula', '')}",
            f"- **{L('Diller', 'Languages')}:** {', '.join(ch['languages'])} ({L('ilk dil ana dil', 'first is main')})",
            f"- **{L('Çocuklara özel', 'Made for kids')}:** {L('Evet', 'Yes') if ch['made_for_kids'] else L('Hayır', 'No')}", "",
            L("## Karakterler", "## Characters"), ""]
    if cfg["characters"]:
        rows += [L("| Kimlik | Ad | Görünüş | Kişilik | Ses |", "| Id | Name | Look | Personality | Voice |"), "| --- | --- | --- | --- | --- |"]
        rows += [f"| `{c['id']}` | {' / '.join(dict.fromkeys(c['name'].values()))} | {c['description']} | {c.get('personality', '')} | "
                 f"{c['voice']['style']} |" for c in cfg["characters"]]
    else:
        rows.append(L("Tekrar eden karakter yok; anlatıcı ağırlıklı.", "No recurring characters; narrator-led."))
    if cfg["narrator"]["enabled"]:
        rows += ["", f"**{L('Anlatıcı', 'Narrator')}:** {cfg['narrator']['voice']['style']}"]
    rows += ["", L("## Görsel stil", "## Visual style"), "", v["style"], "",
             L("## Senaryo kuralları", "## Writing rules"), "", cfg["script"].get("extra_rules") or L(
                 "- (Buraya kendi kurallarınızı yazın: kaçınılacak konular, tekrar eden sahneler, açılış/kapanış cümleleri…)",
                 "- (Add your own rules: topics to avoid, recurring bits, opening/closing lines…)"), ""]
    if design.get("example_style"):
        rows += [L("## Örnek senaryo (üslup referansı)", "## Example script (style reference)"), "",
                 L("Yeni senaryolar bu örneğin üslubunu, ritmini ve uzunluk dengesini izlesin; içeriğini kopyalamasın.",
                   "New scripts follow this example's voice, rhythm and pacing; never copy its content."), "",
                 "```", design["example_style"], "```", ""]
    rows += [L("## Format ve takvim", "## Format and schedule"), "",
             f"- {L('Bölüm', 'Episode')}: ~{cfg['format']['target_minutes']} {L('dk', 'min')}, {cfg['format']['aspect']}; "
             f"Shorts: {cfg['format']['shorts_per_episode']} / {L('bölüm', 'episode')}",
             f"- {L('Üretim', 'Production')}: {cfg['schedule'].get('produce') or L('elle', 'manual')}",
             f"- {L('Yayın saatleri', 'Publish times')}: {', '.join(cfg['schedule']['publish_times'])} ({cfg['schedule']['timezone']})", "",
             L("## SEO", "## SEO"), "",
             f"- {L('Anahtar kelimeler', 'Keywords')}: {', '.join(cfg['seo']['keywords'])}",
             f"- Hashtag: {' '.join('#' + h for h in cfg['seo']['hashtags'])}", "",
             L("## Notlar", "## Notes"), "", ""]
    return "\n".join(rows)


def finish(cfg, design: dict) -> None:
    eid = design.get("test_episode")
    print(t(f"""
{'━' * 60}
  Kurulum tamam.
{'━' * 60}
  Kanal ayarları : kanal/kanal.yaml     Kanal kimliği (yapay zeka bunu okur): kanal/KANAL.md
  Bölüm planı    : kanal/icerik/fikirler.md   Kendi senaryoların: kanal/icerik/senaryolar/
{('  Test videosu   : cikti/' + eid + '/video.mp4') if eid else ''}
Gerisi senin elinde (README.tr.md'de adım adım anlatılıyor):
  python studio.py yeni                 # sıradaki bölümü üret
  python studio.py youtube-baglan       # YouTube hesabını bağla
  python studio.py zamanlayici --kur    # belirlediğin gün/saatlerde otomatik üretim
  python studio.py doktor               # eksik bir şey var mı?""", f"""
{'━' * 60}
  Setup complete.
{'━' * 60}
  Settings: kanal/kanal.yaml     Channel bible (the AI reads it): kanal/KANAL.md
  Episode plan: kanal/icerik/fikirler.md   Your scripts: kanal/icerik/senaryolar/
{('  Test video: cikti/' + eid + '/video.mp4') if eid else ''}
The rest is up to you (step by step in README.md):
  python studio.py new                  # make the next episode
  python studio.py youtube-connect      # connect YouTube
  python studio.py scheduler --install  # automatic production on your days/times
  python studio.py doctor               # anything missing?"""))
