"""Scripts: AI-written, idea-expanded or hand-written (Markdown) → one normalized JSON (senaryo.json).

Script JSON (all text fields are {lang: text} for every channel language):
{
  "title", "summary", "thumbnail_text": {lang: str},
  "topic_keywords": [str],
  "scenes": [{"id", "title": {..}, "location": str (English, visual),
              "shots": [{"id", "visual": str (English image prompt), "characters": [ids left→right],
                         "camera": static|zoom_in|zoom_out|pan_left|pan_right,
                         "lines": [{"speaker": id|narrator, "emotion": str, "text": {lang: str}}],
                         "pause_after": seconds}]}],
  "shorts": [{"title": {..}, "shot_ids": [..]}]
}
"""
from __future__ import annotations

import re
from pathlib import Path

from . import llm
from .util import channel_dir, log, output_dir, read_json, slugify, t

CAMERAS = ["static", "zoom_in", "zoom_out", "pan_left", "pan_right"]
NARRATOR_NAMES = {"narrator", "anlatici", "n", "dis ses", "voiceover", "voice over", "erzahler", "narrateur", "narrador"}

GENRE_RULES = {
    "kids_story": """- Audience is young children: short sentences, concrete words, warm and playful, never scary.
- Open with ONE big question or mystery in the first 15 seconds; answer it only at the end with a clear payoff.
- Invite the viewer to take part (ask them questions, let them spot clues first) and leave a short pause after questions.
- Recap what we know roughly every 3 minutes. One small, gentle lesson, shown through the story, not preached.
- Characters must sound clearly different from each other. End warmly and tease the next episode in dialogue
  (made-for-kids videos have no end screens or comments).""",
    "education": """- Hook with a surprising fact or a question in the first 10 seconds and promise the payoff.
- 3-6 clear sections, each with one idea, a concrete example or analogy, and a one-sentence takeaway.
- Plain language, no filler; recap at the end and a short, honest call to action.""",
    "facts": """- List/countdown structure. Each item starts with its own mini-hook and ends with a twist or 'why it matters'.
- Keep momentum: no long intros, change visuals often, save the best item for last.
- Facts must be accurate and non-controversial; avoid made-up statistics.""",
    "story": """- Clear narrative arc: hook, rising tension, turning point, climax, resolution.
- Vivid sensory detail in narration, distinct character voices, end with a satisfying or intriguing final line.""",
    "sleep": """- Calm, slow, soothing. No conflict, no surprises, no loud moments. Gentle sensory imagery.
- Repetition and long, flowing sentences; narration dominates; dialogue is soft and sparse.
- Gradually slow down towards the end and close with a quiet good-night.""",
    "custom": "- Follow the channel bible closely.",
}


# ---------------------------------------------------------------- JSON schema (built per channel languages/speakers)
def schema(cfg) -> dict:
    L = {"type": "object", "properties": {lg: {"type": "string"} for lg in cfg.langs},
         "required": cfg.langs, "additionalProperties": False}
    line = {"type": "object", "additionalProperties": False, "required": ["speaker", "emotion", "text"],
            "properties": {"speaker": {"type": "string", "enum": cfg.speaker_ids()}, "emotion": {"type": "string"}, "text": L}}
    shot = {"type": "object", "additionalProperties": False,
            "required": ["id", "visual", "characters", "camera", "lines", "pause_after"],
            "properties": {"id": {"type": "string"}, "visual": {"type": "string"},
                           "characters": {"type": "array", "items": {"type": "string"}},
                           "camera": {"type": "string", "enum": CAMERAS},
                           "lines": {"type": "array", "items": line}, "pause_after": {"type": "number"}}}
    scene = {"type": "object", "additionalProperties": False, "required": ["id", "title", "location", "shots"],
             "properties": {"id": {"type": "string"}, "title": L, "location": {"type": "string"},
                            "shots": {"type": "array", "items": shot}}}
    short = {"type": "object", "additionalProperties": False, "required": ["title", "shot_ids"],
             "properties": {"title": L, "shot_ids": {"type": "array", "items": {"type": "string"}}}}
    return {"type": "object", "additionalProperties": False,
            "required": ["title", "summary", "thumbnail_text", "topic_keywords", "scenes", "shorts"],
            "properties": {"title": L, "summary": L, "thumbnail_text": L,
                           "topic_keywords": {"type": "array", "items": {"type": "string"}},
                           "scenes": {"type": "array", "items": scene}, "shorts": {"type": "array", "items": short}}}


def _characters_block(cfg) -> str:
    out = []
    for c in cfg["characters"]:
        names = ", ".join(f"{lg}: {n}" for lg, n in (c.get("name") or {}).items())
        out.append(f"- id `{c['id']}` ({names}): {c.get('description', '')}. Voice/personality: {c.get('voice', {}).get('style', '')}")
    if cfg["narrator"].get("enabled", True):
        out.append("- id `narrator`: off-screen narrator voice.")
    return "\n".join(out) or "- id `narrator`: the only voice (no on-screen characters)."


def system_prompt(cfg) -> str:
    f = cfg["format"]
    words = int(f["target_minutes"] * f["words_per_minute"])
    langs = ", ".join(cfg.langs)
    return f"""You are the head writer and storyboard artist of a YouTube channel. You write complete, original,
production-ready episodes that are then voiced by text-to-speech and illustrated by an image model.

# Channel bible (written by the channel owner, follow it)
{cfg.bible() or cfg['channel'].get('concept', '')}

# Speakers (use only these ids)
{_characters_block(cfg)}

# Genre rules
{GENRE_RULES.get(cfg['channel']['type'], GENRE_RULES['custom'])}
{cfg['script'].get('extra_rules', '')}

# Format
- Languages: {langs}. Every text field is an object with one natural, idiomatic version per language
  (not word-for-word; keep each line's length similar across languages so the voices stay in sync).
- Length: about {words} spoken words in the primary language ({cfg.primary}) ≈ {f['target_minutes']} minutes.
- Split into scenes; split scenes into shots of roughly 6-15 seconds of speech each. Max 30 words per line.
- `visual`: one English image prompt for the shot: who is where, doing what, expression, composition, time of day.
  Mention characters by name with their look; never put written text/letters in the image.
- `location`: short English description of the scene's place (reused for visual consistency).
- `characters`: ids visible in the shot, ordered left to right. `camera`: choose to fit the moment.
- `emotion`: a short English acting direction for the voice (e.g. excited, whispering, curious).
- `pause_after`: seconds of silence after the shot (0 normally, 1-3 after a question to the viewer).
- `shorts`: {f['shorts_per_episode']} self-contained moments for YouTube Shorts (25-55 s), as consecutive shot ids.
- `thumbnail_text`: 2-4 punchy words for the thumbnail. `topic_keywords`: 5-10 search phrases people would type.
- Write text exactly as it should be spoken: no stage directions, emojis or brackets inside `text`.
"""


def _history_block(hist: list[dict]) -> str:
    if not hist:
        return "(no episodes yet)"
    return "\n".join(f"- {h['title']}: {h['summary']}" for h in hist[-40:])


def history(cfg) -> list[dict]:
    out = []
    for p in sorted(output_dir().glob("*/senaryo.json")):
        s = read_json(p, {})
        if s.get("title"):
            out.append(dict(id=p.parent.name, title=s["title"].get(cfg.primary, ""), summary=s.get("summary", {}).get(cfg.primary, "")))
    return out


def generate_ai(cfg, idea: str | None = None) -> dict:
    hist = history(cfg)
    task = (f"Write the next episode based on this idea from the channel owner (keep its spirit, fill in everything else):\n{idea}"
            if idea else "Choose a fresh, high-potential topic for the next episode that fits the channel and is NOT a repeat of earlier episodes, then write it.")
    prompt = f"""{task}

Earlier episodes (do not repeat these topics or plots):
{_history_block(hist)}

Return the complete episode as JSON matching the schema."""
    return normalize(llm.generate(cfg, system_prompt(cfg), prompt, schema(cfg)), cfg)


def revise(cfg, script: dict, feedback: str) -> dict:
    import json
    prompt = f"""Revise this episode according to the channel owner's feedback. Keep everything that the feedback does not touch.

Feedback:
{feedback}

Current episode JSON:
{json.dumps(script, ensure_ascii=False)}

Return the full revised episode as JSON matching the schema."""
    out = normalize(llm.generate(cfg, system_prompt(cfg), prompt, schema(cfg)), cfg)
    out["id"] = script.get("id")
    return out


# ---------------------------------------------------------------- ideas queue (kanal/icerik/fikirler.md)
def ideas_file() -> Path:
    return channel_dir() / "icerik" / "fikirler.md"


def pending_ideas() -> list[str]:
    p = ideas_file()
    if not p.exists():
        return []
    out, used = [], False
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.startswith("## "):
            used = "kullan" in ln.lower() or "used" in ln.lower()
        m = re.match(r"\s*[-*]\s+(?!\[x\])(.+)", ln)
        if m and not used and m.group(1).strip():
            out.append(m.group(1).strip())
    return out


def mark_idea_used(idea: str, episode_id: str) -> None:
    p = ideas_file()
    text = p.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if not re.match(r"\s*[-*]\s+" + re.escape(idea) + r"\s*$", ln)]
    if not any(ln.startswith("## ") and ("kullan" in ln.lower() or "used" in ln.lower()) for ln in lines):
        lines += ["", t("## Kullanılanlar", "## Used")]
    lines.append(f"- [x] {idea}  → {episode_id}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- hand-written scripts (Markdown)
def manual_dir() -> Path:
    return channel_dir() / "icerik" / "senaryolar"


def pending_manual(done_sources: set[str]) -> list[Path]:
    d = manual_dir()
    return [p for p in sorted(d.glob("*.md")) if p.name not in done_sources and not p.name.startswith("_")] if d.exists() else []


def _speaker_map(cfg) -> dict:
    m = {n: "narrator" for n in NARRATOR_NAMES}
    for c in cfg["characters"]:
        m[c["id"].lower()] = c["id"]
        for n in (c.get("name") or {}).values():
            m[_low(n)] = c["id"]
    return m


def _low(s: str) -> str:
    """Case/diacritic-insensitive key: 'KIVIRCIK', 'Kıvırcık' and 'kivircik' all match."""
    import unicodedata
    s = s.replace("İ", "i").replace("I", "i").lower().replace("ı", "i")
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch)).strip()


def parse_markdown(text: str, cfg) -> dict:
    """Parse the simple hand-written format (see ornekler/*/senaryo-ornegi.md).
    Main text is in script.written_language (default: the primary language); `> xx:` lines add other languages."""
    langs = cfg.langs
    P = cfg["script"].get("written_language") or cfg.primary
    spk = _speaker_map(cfg)
    s = dict(title={}, summary={}, thumbnail_text={}, topic_keywords=[], scenes=[], shorts=[])
    scene = shot = line = None
    last = None  # what a "> xx:" translation line attaches to
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln:
            continue
        if ln.startswith("# "):
            s["title"][P] = ln[2:].strip(); last = s["title"]; continue
        m = re.match(r">\s*([a-z]{2})\s*:\s*(.*)", ln)
        if m:
            if last is not None:
                last[m.group(1)] = m.group(2).strip()
            continue
        if ln.startswith("## "):
            title, _, loc = ln[3:].partition("|")
            scene = dict(id=f"S{len(s['scenes']) + 1}", title={P: title.strip()}, location=(loc or title).strip(), shots=[])
            s["scenes"].append(scene); shot = None; last = scene["title"]; continue
        if scene is None:
            scene = dict(id="S1", title={P: s["title"].get(P, "")}, location="", shots=[]); s["scenes"].append(scene)
        m = re.match(r"\[(?:görsel|gorsel|visual|image)\s*:\s*(.*?)\]\s*(?:<!--.*-->)?$", ln, re.I)
        if m:
            shot = dict(id="", visual=m.group(1).strip(), characters=[], camera="", lines=[], pause_after=0)
            scene["shots"].append(shot); last = None; continue
        m = re.match(r"\((?:bekle|pause|wait)\s*([\d.]+)[^)]*\)$", ln, re.I)
        if m:
            if shot:
                shot["pause_after"] = float(m.group(1))
            continue
        m = re.match(r"\*{0,2}([^:()*]{1,40}?)\s*(?:\(([^)]*)\))?\s*\*{0,2}\s*:\s*\*{0,2}\s*(.+)", ln)
        if m and _low(m.group(1)) in spk:
            if shot is None or (not shot["visual"] and len(shot["lines"]) >= 3):
                shot = dict(id="", visual="", characters=[], camera="", lines=[], pause_after=0)
                scene["shots"].append(shot)
            line = dict(speaker=spk[_low(m.group(1))], emotion=(m.group(2) or "").strip(), text={P: m.group(3).strip()})
            shot["lines"].append(line); last = line["text"]; continue
        if line is not None and last is line["text"] and not ln.startswith(("#", "[", "(")):
            line["text"][P] += " " + ln     # continuation of the previous line
    s["scenes"] = [sc for sc in s["scenes"] if sc["shots"]]
    if not s["scenes"]:
        raise ValueError(t("Senaryoda hiç replik bulunamadı. Biçim: 'KARAKTER: metin'", "No dialogue found. Format: 'CHARACTER: text'"))
    missing = [lg for lg in langs if lg != P and any(lg not in l["text"] for sc in s["scenes"] for sh in sc["shots"] for l in sh["lines"])]
    s["_missing_langs"] = missing
    s["_written"] = P
    return s


def complete_manual(cfg, parsed: dict) -> dict:
    """Fill translations, visuals and Shorts with the AI while keeping the owner's primary-language text verbatim."""
    import json
    missing = parsed.pop("_missing_langs", [])
    W = parsed.pop("_written", cfg.primary)
    if llm.available(cfg):
        prompt = f"""The channel owner wrote this episode by hand. Complete it as production-ready JSON:
- Keep every line's {W} text EXACTLY as written, same order, same speakers. Do not add or remove lines.
- Fill in any missing language versions{(' (' + ', '.join(missing) + ')') if missing else ''}, missing titles, summary,
  thumbnail_text, topic_keywords, image prompts (`visual`), cameras, characters and Shorts.
- You may split or merge shots, but the sequence of lines must stay identical.

Parsed draft:
{json.dumps(parsed, ensure_ascii=False)}"""
        try:
            out = normalize(llm.generate(cfg, system_prompt(cfg), prompt, schema(cfg)), cfg)
            if _line_texts(out, W) == _line_texts(parsed, W):
                return out
            log(t("Yapay zeka metni değiştirdi; yalnız çevirileri alıyorum.", "The AI changed the text; keeping only its translations."))
            src, ai = _lines(parsed), _lines(out)
            if len(src) == len(ai):
                for a, b in zip(src, ai):
                    for lg in cfg.langs:
                        a["text"].setdefault(lg, b["text"].get(lg, ""))
        except llm.LLMUnavailable:
            pass
    elif missing:
        log(t(f"Uyarı: {missing} çevirisi yok ve yapay zeka kapalı; bu diller birincil dilin metniyle seslendirilmeyecek.",
              f"Warning: {missing} translations missing and no AI configured; those languages will be skipped."))
        cfg["channel"]["languages"] = [lg for lg in cfg.langs if lg not in missing]
    return normalize(parsed, cfg, fill_from=W)


def _lines(s):
    return [l for sc in s["scenes"] for sh in sc["shots"] for l in sh["lines"]]


def _line_texts(s, lg):
    return [re.sub(r"\W+", " ", l["text"].get(lg, "")).strip().lower() for l in _lines(s)]


# ---------------------------------------------------------------- normalization / checks
def normalize(s: dict, cfg, fill_from: str | None = None) -> dict:
    """Make any script (AI or manual) safe to render: ids, speakers, cameras, translations, Shorts.
    Missing language versions are copied from `fill_from` (default: primary language)."""
    langs = cfg.langs
    P = fill_from if fill_from in langs else cfg.primary
    valid = set(cfg.speaker_ids()) | {"narrator"}
    chars = {c["id"] for c in cfg["characters"]}
    for key in ("title", "summary", "thumbnail_text"):
        s[key] = s.get(key) or {}
        for lg in langs:
            s[key].setdefault(lg, s[key].get(P, ""))
    s.setdefault("topic_keywords", [])
    cams = iter(["zoom_in", "static", "pan_right", "zoom_out", "static", "pan_left"] * 999)
    for i, sc in enumerate(s["scenes"], 1):
        sc["id"] = f"S{i}"
        sc.setdefault("title", {}); sc.setdefault("location", "")
        for lg in langs:
            sc["title"].setdefault(lg, sc["title"].get(P, ""))
        for j, sh in enumerate(sc["shots"], 1):
            sh["id"] = f"S{i}_{j:02d}"
            sh["camera"] = sh.get("camera") if sh.get("camera") in CAMERAS else next(cams)
            sh["pause_after"] = float(sh.get("pause_after") or 0)
            for l in sh.get("lines", []):
                if l.get("speaker") not in valid:
                    l["speaker"] = "narrator"
                l.setdefault("emotion", "")
                for lg in langs:
                    if not l["text"].get(lg):
                        l["text"][lg] = l["text"].get(P, "")
                l["text"] = {lg: _clean_line(l["text"][lg]) for lg in langs}
            sh["lines"] = [l for l in sh.get("lines", []) if l["text"].get(P)]
            present = [c for c in sh.get("characters", []) if c in chars]
            if not present:
                present = list(dict.fromkeys(l["speaker"] for l in sh["lines"] if l["speaker"] in chars))
            sh["characters"] = present
            if not sh.get("visual"):
                who = ", ".join(_char_name(cfg, c) for c in present)
                sh["visual"] = f"{sc['location'] or sc['title'].get(P, '')}" + (f", {who}" if who else "")
    ids = [sh["id"] for sc in s["scenes"] for sh in sc["shots"]]
    shorts = []
    for x in s.get("shorts", []):
        keep = [i for i in x.get("shot_ids", []) if i in ids]
        if keep:
            title = x.get("title") or {}
            shorts.append(dict(title={lg: title.get(lg) or title.get(P) or s["title"][lg] for lg in langs}, shot_ids=keep))
    if not shorts:      # no AI picks: one Short per scene from the start (render trims to < 60 s)
        shorts = [dict(title={lg: sc["title"][lg] or s["title"][lg] for lg in langs}, shot_ids=[sh["id"] for sh in sc["shots"]])
                  for sc in s["scenes"] if any(sh["lines"] for sh in sc["shots"])]
    s["shorts"] = shorts[: max(0, cfg["format"]["shorts_per_episode"])]
    return s


def _clean_line(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]|\*+|[\U0001F300-\U0001FAFF]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _char_name(cfg, cid):
    c = cfg.character(cid)
    return (c.get("name") or {}).get("en") or next(iter((c.get("name") or {cid: cid}).values())) if c else cid


def stats(s: dict, cfg) -> dict:
    words = {lg: sum(len(l["text"][lg].split()) for l in _lines(s)) for lg in cfg.langs}
    return dict(scenes=len(s["scenes"]), shots=sum(len(sc["shots"]) for sc in s["scenes"]), lines=len(_lines(s)),
                words=words, est_minutes=round(words[cfg.primary] / cfg["format"]["words_per_minute"], 1))


def episode_id(s: dict, cfg) -> str:
    import datetime
    return f"{datetime.date.today():%Y%m%d}-{slugify(s['title'].get(cfg.primary) or 'video', 40)}"


def to_markdown(s: dict, cfg) -> str:
    """Readable review copy (senaryo.md) for approval."""
    P = cfg.primary
    st = stats(s, cfg)
    out = [f"# {s['title'][P]}", ""]
    out += [f"> {lg}: {s['title'][lg]}" for lg in cfg.langs if lg != P]
    out += ["", f"_{s['summary'][P]}_", "",
            t(f"Sahne {st['scenes']} · çekim {st['shots']} · replik {st['lines']} · ~{st['est_minutes']} dk · kelime {st['words']}",
              f"Scenes {st['scenes']} · shots {st['shots']} · lines {st['lines']} · ~{st['est_minutes']} min · words {st['words']}"), ""]
    for sc in s["scenes"]:
        out += [f"## {sc['title'][P]} | {sc['location']}", ""]
        for sh in sc["shots"]:
            out.append(f"[visual: {sh['visual']}]  <!-- {sh['id']} · {sh['camera']} -->")
            for l in sh["lines"]:
                name = "ANLATICI" if l["speaker"] == "narrator" and P == "tr" else ("NARRATOR" if l["speaker"] == "narrator" else _char_name_lang(cfg, l["speaker"], P).upper())
                emo = f" ({l['emotion']})" if l["emotion"] else ""
                out.append(f"{name}{emo}: {l['text'][P]}")
                out += [f"> {lg}: {l['text'][lg]}" for lg in cfg.langs if lg != P]
            if sh["pause_after"]:
                out.append(f"(pause {sh['pause_after']:g})")
            out.append("")
    if s["shorts"]:
        out += ["## Shorts", ""] + [f"- {x['title'][P]}: {', '.join(x['shot_ids'])}" for x in s["shorts"]]
    return "\n".join(out) + "\n"


def _char_name_lang(cfg, cid, lg):
    c = cfg.character(cid) or {}
    names = c.get("name") or {}
    return names.get(lg) or next(iter(names.values()), cid)
