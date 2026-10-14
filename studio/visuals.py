"""Shot images: AI-generated (Gemini / OpenAI), your own files, or offline placeholders.

Images are stored per episode in cikti/<id>/gorseller/<shot or scene id>.png at 2560x1440 (16:9) or 1440x2560 (9:16).
Character consistency: put a reference picture at kanal/karakterler/<id>/referans.png (the wizard can draw one);
Gemini receives it with every shot that shows the character.
"""
from __future__ import annotations

import base64
import io
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .util import channel_dir, http_json, log, secret, t

DEFAULT_MODELS = {"gemini": "gemini-2.5-flash-image", "openai": "gpt-image-1"}
USAGE: list[dict] = []


def canvas_size(aspect: str) -> tuple[int, int]:
    return (2560, 1440) if aspect == "16:9" else (1440, 2560)


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for f in (["DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"] if bold else ["DejaVuSans.ttf", "arial.ttf", "Arial.ttf"]):
        for d in ("", "/usr/share/fonts/truetype/dejavu/", "C:/Windows/Fonts/", "/Library/Fonts/", "/System/Library/Fonts/Supplemental/"):
            try:
                return ImageFont.truetype(d + f, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def cover(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    W, H = size
    s = max(W / im.width, H / im.height)
    im = im.convert("RGB").resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    x, y = (im.width - W) // 2, (im.height - H) // 2
    return im.crop((x, y, x + W, y + H))


def reference(cid: str) -> Path | None:
    d = channel_dir() / "karakterler" / cid
    for n in ("referans.png", "referans.jpg", "reference.png", "reference.jpg"):
        if (d / n).exists():
            return d / n
    return None


# ---------------------------------------------------------------- prompts
def shot_prompt(cfg, scene: dict, shot: dict, background_only: bool = False) -> str:
    v = cfg["visuals"]
    kids = cfg["channel"].get("made_for_kids")
    parts = [f"Style: {v['style']}."]
    if background_only:
        parts.append(f"Empty background plate of: {scene['location'] or shot['visual']}. No people, no animals, no characters. "
                     "Leave open ground in the lower middle for characters to stand.")
    else:
        parts.append(f"Scene: {shot['visual']}.")
        if scene.get("location"):
            parts.append(f"Setting: {scene['location']}.")
        for cid in shot.get("characters", []):
            c = cfg.character(cid)
            if c:
                name = (c.get("name") or {}).get("en") or next(iter((c.get("name") or {cid: cid}).values()))
                ref = " (match the reference image exactly)" if reference(cid) else ""
                parts.append(f"{name}: {c.get('description', '')}{ref}.")
    parts.append(f"{'Wide 16:9' if cfg['format']['aspect'] == '16:9' else 'Vertical 9:16'} composition, "
                 "cinematic framing, no text, no letters, no captions, no watermark, no logo.")
    if kids:
        parts.append("Friendly and safe for young children.")
    return " ".join(parts)


# ---------------------------------------------------------------- providers
def _gemini(cfg, prompt: str, refs: list[Path]) -> Image.Image:
    from .llm import gemini_url
    model = cfg["visuals"].get("model") or DEFAULT_MODELS["gemini"]
    url, hd = gemini_url(cfg, model, secret("GEMINI_API_KEY"))
    parts = [dict(inlineData=dict(mimeType="image/png" if p.suffix == ".png" else "image/jpeg",
                                  data=base64.b64encode(p.read_bytes()).decode())) for p in refs]
    parts.append(dict(text=prompt))
    body = dict(contents=[dict(role="user", parts=parts)],
                generationConfig=dict(responseModalities=["IMAGE"], imageConfig=dict(aspectRatio=cfg["format"]["aspect"])))
    r = http_json(url, body, hd, timeout=300)
    for p in r.get("candidates", [{}])[0].get("content", {}).get("parts", []):
        if "inlineData" in p:
            USAGE.append(dict(provider="gemini", model=model))
            return Image.open(io.BytesIO(base64.b64decode(p["inlineData"]["data"])))
    raise RuntimeError(t("Görsel üretilemedi: ", "No image returned: ") + str(r)[:300])


def _openai(cfg, prompt: str, refs: list[Path]) -> Image.Image:
    model = cfg["visuals"].get("model") or DEFAULT_MODELS["openai"]
    size = "1536x1024" if cfg["format"]["aspect"] == "16:9" else "1024x1536"
    r = http_json("https://api.openai.com/v1/images/generations", dict(model=model, prompt=prompt, size=size, n=1),
                  {"Authorization": f"Bearer {secret('OPENAI_API_KEY')}"}, timeout=300)
    USAGE.append(dict(provider="openai", model=model))
    return Image.open(io.BytesIO(base64.b64decode(r["data"][0]["b64_json"])))


PALETTE = [(255, 183, 77), (129, 199, 132), (100, 181, 246), (240, 98, 146), (186, 104, 200), (77, 208, 225)]


def placeholder(cfg, scene: dict, shot: dict, size, background_only=False) -> Image.Image:
    """Offline stand-in: soft gradient, scene title, characters as labelled discs. Useful for testing timing."""
    W, H = size
    seed = sum(map(ord, scene["id"]))
    c1, c2 = PALETTE[seed % 6], PALETTE[(seed + 2) % 6]
    im = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(im)
    for y in range(H):
        k = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(a * (1 - k) + b * k) for a, b in zip(c1, c2)))
    d.ellipse((W * .62, H * .08, W * .8, H * .08 + W * .18), fill=(255, 245, 200))
    d.rectangle((0, H * .78, W, H), fill=tuple(max(0, x - 60) for x in c2))
    if not background_only:
        chars = shot.get("characters", [])
        for i, cid in enumerate(chars):
            x = W * (i + 1) / (len(chars) + 1)
            r = W * .07
            col = PALETTE[(sum(map(ord, cid)) + 1) % 6]
            d.ellipse((x - r, H * .72 - 2 * r, x + r, H * .72), fill=col, outline=(40, 30, 60), width=8)
            d.text((x, H * .72 + 30), (cfg.character(cid) or {}).get("name", {}).get(cfg.primary, cid), font=font(64), fill=(30, 25, 50), anchor="ma")
    title = scene["title"].get(cfg.primary, "")
    d.text((W / 2, H * .1), title, font=font(96), fill=(40, 30, 60), anchor="ma")
    if not background_only:
        for i, ln in enumerate(textwrap.wrap(shot["visual"], 60)[:3]):
            d.text((W / 2, H * .22 + i * 60), ln, font=font(44, False), fill=(50, 40, 70), anchor="ma")
    return im.filter(ImageFilter.SMOOTH)


# ---------------------------------------------------------------- per episode
def plan(cfg, script: dict) -> list[tuple[str, dict, dict]]:
    """→ [(image name, scene, shot)] — one per shot, or one per scene when visuals.per == scene / puppet mode."""
    per_scene = cfg["visuals"]["per"] == "scene" or cfg["visuals"]["mode"] == "puppet"
    out = []
    for sc in script["scenes"]:
        if per_scene:
            out.append((sc["id"], sc, sc["shots"][0]))
        else:
            out += [(sh["id"], sc, sh) for sh in sc["shots"]]
    return out


def image_for(shot_id: str, scene_id: str, folder: Path) -> Path | None:
    for name in (shot_id, scene_id):
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            if (folder / f"{name}{ext}").exists():
                return folder / f"{name}{ext}"
    return None


def make_all(cfg, script: dict, folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    mode = cfg["visuals"]["mode"]
    size = canvas_size(cfg["format"]["aspect"])
    puppet = mode == "puppet"
    todo = [(n, sc, sh) for n, sc, sh in plan(cfg, script) if not image_for(n, sc["id"], folder)]
    if mode == "local_images" and todo:
        names = ", ".join(n for n, _, _ in todo[:12])
        raise SystemExit(t(f"Şu görseller eksik: {names} … Klasör: {folder}  (çekim ya da sahne kimliğiyle .png/.jpg koyun)",
                           f"Missing images: {names} … Folder: {folder}  (name them by shot or scene id, .png/.jpg)"))
    prov = cfg["visuals"]["provider"]
    for i, (name, sc, sh) in enumerate(todo, 1):
        if mode == "placeholder" or (puppet and cfg["visuals"].get("provider") == "placeholder"):
            im = placeholder(cfg, sc, sh, size, background_only=puppet)
        else:
            prompt = shot_prompt(cfg, sc, sh, background_only=puppet)
            refs = [] if puppet else [r for r in (reference(c) for c in sh.get("characters", [])) if r][:3]
            log(t(f"görsel {i}/{len(todo)}: {name}", f"image {i}/{len(todo)}: {name}"))
            im = (_gemini if prov == "gemini" else _openai)(cfg, prompt, refs)
        cover(im, size).save(folder / f"{name}.png", optimize=True)
    return dict(images=len(plan(cfg, script)), generated=len(todo) if mode in ("ai_images", "puppet") and prov != "placeholder" else 0)


def make_reference(cfg, cid: str) -> Path:
    """Draw a character reference sheet once (used for consistency in every later image)."""
    c = cfg.character(cid)
    d = channel_dir() / "karakterler" / cid
    d.mkdir(parents=True, exist_ok=True)
    name = (c.get("name") or {}).get("en") or cid
    prompt = (f"Style: {cfg['visuals']['style']}. Character reference sheet of {name}: {c.get('description', '')}. "
              "Full body, front view and three-quarter view side by side, neutral friendly expression, plain white background, "
              "no text, no letters.")
    im = (_gemini if cfg["visuals"]["provider"] == "gemini" else _openai)(cfg, prompt, [])
    im.convert("RGB").save(d / "referans.png")
    return d / "referans.png"
