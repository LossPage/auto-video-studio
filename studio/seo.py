"""YouTube metadata per language (title, description with chapters, tags, hashtags) + thumbnail.

The AI writes it from the actual script; code then enforces YouTube's limits and good practice:
title ≤ 100 chars (aim ≤ 70, key phrase early), no < > characters, description ≤ 5000 with a strong first
150 characters, chapters (first at 0:00, ≥ 3, ≥ 10 s apart), tags ≤ 500 chars total, ≤ 15 hashtags.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from . import llm
from .util import fmt_ts, log, t
from .visuals import cover, font, image_for


def _schema(cfg, n_shorts):
    meta = {"type": "object", "additionalProperties": False, "required": ["title", "description", "tags", "hashtags"],
            "properties": {"title": {"type": "string"}, "description": {"type": "string"},
                           "tags": {"type": "array", "items": {"type": "string"}},
                           "hashtags": {"type": "array", "items": {"type": "string"}}}}
    per_lang = {"type": "object", "additionalProperties": False, "required": cfg.langs, "properties": {lg: meta for lg in cfg.langs}}
    return {"type": "object", "additionalProperties": False, "required": ["video", "shorts"],
            "properties": {"video": per_lang, "shorts": {"type": "array", "items": per_lang}}}


SYSTEM = """You are a YouTube SEO specialist. You write metadata that ranks in YouTube search and gets clicked,
without clickbait lies. Rules:
- Title: the main search phrase within the first 40 characters, curiosity or clear benefit, natural language,
  ideally 45-70 characters, never over 100. No ALL CAPS words except short acronyms, no emoji spam.
- Description: first 150 characters = hook + main search phrase (this shows in search results). Then 2-3 short
  paragraphs about what the viewer gets, using related search phrases naturally. No keyword lists. Do NOT add
  chapters/timestamps (added automatically). End with 3 relevant hashtags on their own line.
- Tags: 10-18 tags, mixing the exact main phrase, broader topic tags, long-tail variations and the channel name.
- Hashtags: 3-6, no spaces, specific to this video and channel.
- Write each language natively for searchers in that language (what they would actually type), not a translation.
- Shorts: title ≤ 60 characters, punchy, include #Shorts in hashtags.
- Respect the audience: for children's content keep it gentle and parent-friendly."""


def generate(cfg, script: dict, info: dict) -> dict:
    P = cfg.primary
    n_shorts = len({s["n"] for s in info.get("shorts", [])})
    if llm.available(cfg):
        prompt = f"""Channel: {cfg['channel']['name']}
Channel bible:
{cfg.bible() or cfg['channel'].get('concept', '')}

Channel keywords: {', '.join(cfg['seo'].get('keywords', []))}
Made for kids: {cfg['channel'].get('made_for_kids')}

Episode (titles/summaries per language): {json.dumps({k: script[k] for k in ('title', 'summary')}, ensure_ascii=False)}
Search phrases from the writer: {', '.join(script.get('topic_keywords', []))}
Scenes: {json.dumps([sc['title'].get(P) for sc in script['scenes']], ensure_ascii=False)}
Duration: {int(info.get('duration', 0) // 60)} min.
Shorts ({n_shorts}): {json.dumps([s['title'] for s in script.get('shorts', [])][:n_shorts], ensure_ascii=False)}

Write metadata for the video and for each Short, in these languages: {', '.join(cfg.langs)}."""
        try:
            meta = llm.generate(cfg, SYSTEM, prompt, _schema(cfg, n_shorts), max_tokens=16000)
            return polish(cfg, meta, script, info)
        except Exception as e:  # SEO must never block a finished video
            log(t(f"SEO yapay zeka hatası, basit metin kullanılıyor: {e}", f"SEO AI error, using a simple fallback: {e}"))
    return polish(cfg, fallback(cfg, script, n_shorts), script, info)


def fallback(cfg, script, n_shorts) -> dict:
    tags = list(dict.fromkeys(script.get("topic_keywords", []) + cfg["seo"].get("keywords", [])))
    tagsx = [x.lstrip("#") for x in cfg["seo"].get("hashtags", [])]
    def about(lg):
        if script["summary"].get(lg):
            return script["summary"][lg]
        first = [l["text"][lg] for sc in script["scenes"] for sh in sc["shots"] for l in sh["lines"]][:3]
        return " ".join(first)
    video = {lg: dict(title=script["title"][lg], description=about(lg), tags=tags, hashtags=tagsx) for lg in cfg.langs}
    shorts = [{lg: dict(title=s["title"][lg], description=about(lg), tags=tags, hashtags=tagsx + ["Shorts"]) for lg in cfg.langs}
              for s in script.get("shorts", [])[:n_shorts]]
    return dict(video=video, shorts=shorts)


# ---------------------------------------------------------------- limits & good practice
def clean_title(x: str, limit=100) -> str:
    x = re.sub(r"[<>]", "", re.sub(r"\s+", " ", x)).strip().strip('"')
    if len(x) > limit:
        x = x[:limit].rsplit(" ", 1)[0].rstrip(",;:-–")
    return x


def clean_tags(tags: list[str], channel_name: str, extra: list[str]) -> list[str]:
    out, seen, total = [], set(), 0
    for tg in list(tags) + list(extra) + [channel_name]:
        tg = re.sub(r"[<>,#\"]", "", str(tg)).strip()
        if not tg or tg.lower() in seen or len(tg) > 60:
            continue
        cost = len(tg) + (2 if " " in tg else 0) + 1        # YouTube counts quotes around multi-word tags + comma
        if total + cost > 480:
            continue
        seen.add(tg.lower()); out.append(tg); total += cost
    return out


def clean_hashtags(hs: list[str], limit=15) -> list[str]:
    out = []
    for x in hs:
        x = "#" + re.sub(r"[^\w]", "", str(x).replace(" ", ""), flags=re.UNICODE)
        if len(x) > 1 and x.lower() not in [o.lower() for o in out]:
            out.append(x)
    return out[:limit]


def chapters_text(chs: list[tuple[float, str]], total: float) -> str:
    """YouTube chapters: first at 0:00, at least 3, each at least 10 s long."""
    rows = []
    for s, title in chs:
        if not title:
            continue
        s = 0.0 if not rows else s
        if rows and s - rows[-1][0] < 10:
            continue
        rows.append((s, title))
    if rows and total - rows[-1][0] < 10:
        rows.pop()
    if len(rows) < 3:
        return ""
    return "\n".join(f"{fmt_ts(s, srt=False)} {tt}" for s, tt in rows)


def _desc(cfg, body: str, hashtags: list[str], lang: str, chapters: str = "") -> str:
    body = re.sub(r"[<>]", "", body).strip()
    body = re.sub(r"(\n#\S+(\s+#\S+)*\s*)+$", "", body).strip()          # hashtags are re-added below
    footer = (cfg["seo"].get("description_footer") or {}).get(lang, "")
    parts = [body]
    if chapters:
        parts.append(chapters)
    if footer:
        parts.append(footer.strip())
    if hashtags:
        parts.append(" ".join(hashtags[:5]))
    return "\n\n".join(parts)[:5000]


def polish(cfg, meta: dict, script: dict, info: dict) -> dict:
    name = cfg["channel"]["name"]
    out = dict(video={}, shorts=[])
    for lg in cfg.langs:
        m = meta["video"].get(lg) or meta["video"][cfg.primary]
        hs = clean_hashtags(m.get("hashtags", []) + cfg["seo"].get("hashtags", []))
        ch = chapters_text(info.get("chapters", {}).get(lg, []), info.get("duration", 0))
        out["video"][lg] = dict(title=clean_title(m["title"]), description=_desc(cfg, m["description"], hs, lg, ch),
                                tags=clean_tags(m.get("tags", []), name, cfg["seo"].get("default_tags", [])), hashtags=hs)
    for sm in meta.get("shorts", []):
        d = {}
        for lg in cfg.langs:
            m = sm.get(lg) or sm.get(cfg.primary)
            hs = clean_hashtags(["Shorts"] + m.get("hashtags", []))
            d[lg] = dict(title=clean_title(m["title"], 100), description=_desc(cfg, m["description"], hs, lg),
                         tags=clean_tags(m.get("tags", []), name, cfg["seo"].get("default_tags", [])), hashtags=hs)
        out["shorts"].append(d)
    out["warnings"] = [f"{lg}: title {len(v['title'])} chars (>70 gets cut in search)" for lg, v in out["video"].items() if len(v["title"]) > 70]
    return out


# ---------------------------------------------------------------- thumbnail
def thumbnail(cfg, script: dict, ep: Path, lang: str) -> Path:
    """1280x720 JPG: the most character-rich shot + big outlined text (thumbnail_text)."""
    shots = [(sc, sh) for sc in script["scenes"] for sh in sc["shots"]]
    sc, sh = max(shots[1:] or shots, key=lambda x: (len(x[1].get("characters", [])), -shots.index(x)))
    img = Image.open(image_for(sh["id"], sc["id"], ep / "gorseller"))
    if cfg["visuals"]["mode"] == "puppet":           # backgrounds are empty in puppet mode: add the characters
        from . import puppet
        chars = [c for c in sh.get("characters", []) if puppet.has_pack(c)] or [c["id"] for c in cfg["characters"] if puppet.has_pack(c["id"])][:3]
        img = img.convert("RGBA")
        puppet.composite(img, chars, puppet.stage(chars, []), 0.0, 0, {}, 1)
    base = cover(img, (1280, 720))
    base = Image.blend(base, base.filter(ImageFilter.UnsharpMask(3, 120)), .6)
    d = ImageDraw.Draw(base)
    text = (script.get("thumbnail_text") or {}).get(lang) or script["title"][lang]
    words = text.upper() if lang not in ("tr", "az") else text.replace("i", "İ").upper()
    lines, cur = [], ""
    for w in words.split():
        if len(cur) + len(w) > 14 and cur:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    lines = (lines + [cur])[:3]
    fs = 120 if len(lines) == 1 else 100 if len(lines) == 2 else 84
    y = 720 - 40 - len(lines) * fs * 1.08
    for i, ln in enumerate(lines):
        d.text((48, y + i * fs * 1.08), ln, font=font(fs), fill=(255, 230, 60), stroke_width=10, stroke_fill=(20, 16, 36))
    out = ep / f"kapak.{lang}.jpg"
    base.save(out, quality=90)
    return out
