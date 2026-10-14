"""Puppet mode: layered PNG characters with lip sync, blinking and breathing over scene backgrounds.

Character pack (all PNGs the same canvas size, transparent, character facing RIGHT):
  kanal/karakterler/<id>/kukla/
    govde.png            body + face WITHOUT a mouth (or with a closed mouth)
    agiz_X.png … agiz_H.png   mouth layers (Rhubarb shapes). Minimum: agiz_X.png (closed) + agiz_D.png (open)
    goz_kapali.png       optional closed-eyes layer for blinking
    kukla.json           optional: {"height": 0.42}  (character height as a fraction of frame height)
`python studio.py kukla-ornek` writes a simple example pack for every character so the mode works immediately.
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .util import SR, cache_dir, channel_dir, h, load_audio

VISEMES = "XABCDEFGH"
FALLBACK = {"A": "X", "B": "C", "C": "D", "E": "D", "F": "E", "G": "B", "H": "C", "D": "C"}
FPS_ANIM = 12


def pack_dir(cid: str) -> Path:
    return channel_dir() / "karakterler" / cid / "kukla"


def has_pack(cid: str) -> bool:
    return (pack_dir(cid) / "govde.png").exists()


@lru_cache(maxsize=None)
def load_pack(cid: str, height_px: int) -> dict:
    d = pack_dir(cid)
    meta = json.loads((d / "kukla.json").read_text()) if (d / "kukla.json").exists() else {}
    body = Image.open(d / "govde.png").convert("RGBA")
    bb = body.getbbox() or (0, 0, body.width, body.height)
    s = height_px / max(1, bb[3] - bb[1])

    def prep(im):
        im = im.convert("RGBA").crop(bb)
        return im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)

    mouths = {v: prep(Image.open(d / f"agiz_{v}.png")) for v in VISEMES if (d / f"agiz_{v}.png").exists()}
    eyes = prep(Image.open(d / "goz_kapali.png")) if (d / "goz_kapali.png").exists() else None
    return dict(body=prep(body), mouths=mouths, eyes=eyes, height=meta.get("height", .42))


def mouth_layer(pack: dict, v: str):
    m = pack["mouths"]
    seen = set()
    while v not in m and v not in seen:
        seen.add(v)
        v = FALLBACK.get(v, "X")
    return m.get(v) or m.get("X") or (next(iter(m.values())) if m else None)


# ---------------------------------------------------------------- lip sync cues
def cues(wav: str) -> list[tuple[float, float, str]]:
    """Mouth shapes over time. Rhubarb Lip Sync when installed (best), otherwise loudness-based shapes."""
    key = cache_dir() / "lipsync" / f"{h('cues1', wav)}.json"
    if key.exists():
        return [tuple(x) for x in json.loads(key.read_text())]
    key.parent.mkdir(parents=True, exist_ok=True)
    rb = shutil.which("rhubarb")
    out = None
    if rb:
        r = subprocess.run([rb, "-r", "phonetic", "-f", "json", "-q", wav], capture_output=True, text=True)
        if r.returncode == 0:
            out = [(c["start"], c["end"], c["value"]) for c in json.loads(r.stdout)["mouthCues"]]
    if out is None:
        a = load_audio(Path(wav), 16000)
        fr = 16000 // 24
        n = len(a) // fr
        rms = np.sqrt(np.mean(a[:n * fr].reshape(n, fr) ** 2, axis=1)) if n else np.zeros(1)
        ref = np.percentile(rms, 90) + 1e-6
        out, prev = [], None
        for i, r in enumerate(rms):
            k = r / ref
            v = "X" if k < .12 else "B" if k < .35 else "C" if k < .7 else "D"
            if v == prev and out:
                out[-1] = (out[-1][0], (i + 1) / 24, v)
            else:
                out.append((i / 24, (i + 1) / 24, v))
            prev = v
    key.write_text(json.dumps(out))
    return out


def shape_at(cs, t: float) -> str:
    for s, e, v in cs:
        if s <= t < e:
            return v
    return "X"


# ---------------------------------------------------------------- staging
def stage(shot_chars: list[str], lines: list[dict]) -> dict:
    """Positions left→right and facing per line: the speaker faces the nearest listener, listeners face the speaker."""
    n = len(shot_chars)
    xs = {c: (i + 1) / (n + 1) for i, c in enumerate(shot_chars)}
    facing = []
    default = {c: (1 if xs[c] < .5 else -1) if n > 1 else 1 for c in shot_chars}
    for l in lines or [None]:
        sp = l["speaker"] if l else None
        f = dict(default)
        if sp in xs:
            others = [c for c in shot_chars if c != sp]
            if others:
                tgt = min(others, key=lambda c: abs(xs[c] - xs[sp]))
                f[sp] = 1 if xs[tgt] > xs[sp] else -1
            for c in others:
                f[c] = 1 if xs[sp] > xs[c] else -1
        facing.append(f)
    return dict(x=xs, facing=facing)


def composite(canvas: Image.Image, shot_chars, st, t: float, line_idx: int, talking: dict, blink_seed: int):
    """Draw puppets onto `canvas` (in place) at time t. talking = {cid: viseme}"""
    W, H = canvas.size
    for i, cid in enumerate(shot_chars):
        if not has_pack(cid):
            continue
        meta_h = load_pack(cid, 10)["height"]
        pk = load_pack(cid, int(H * meta_h))
        f = st["facing"][min(line_idx, len(st["facing"]) - 1)][cid]
        v = talking.get(cid, "X")
        layers = [pk["body"], mouth_layer(pk, v)]
        rnd = random.Random(blink_seed + i * 97)
        tb = rnd.uniform(.5, 2.5)
        blink = False
        while tb < t + 1:
            if tb <= t < tb + .15:
                blink = True
            tb += rnd.uniform(2.2, 5.0)
        if blink and pk["eyes"] is not None:
            layers.append(pk["eyes"])
        im = pk["body"].copy()
        for L in layers[1:]:
            if L is not None:
                im.alpha_composite(L)
        if f < 0:
            im = im.transpose(Image.FLIP_LEFT_RIGHT)
        bob = 4 * np.sin(2 * np.pi * t / 3.2 + i) + (-6 if v not in ("X", "A") else 0)
        x = int(st["x"][cid] * W - im.width / 2)
        y = int(H * .93 - im.height + bob)
        shadow = Image.new("RGBA", (im.width, 40), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).ellipse((im.width * .15, 5, im.width * .85, 35), fill=(0, 0, 0, 55))
        canvas.alpha_composite(shadow, (x, int(H * .93) - 20))
        canvas.alpha_composite(im, (x, y))


# ---------------------------------------------------------------- example pack (replace with your own art)
def build_example_pack(cid: str, color=(255, 152, 67), size=900) -> Path:
    d = pack_dir(cid)
    d.mkdir(parents=True, exist_ok=True)
    W = H = size
    dark = tuple(max(0, c - 90) for c in color)
    light = tuple(min(255, c + 60) for c in color)

    def base():
        return Image.new("RGBA", (W, H), (0, 0, 0, 0))

    body = base(); d_ = ImageDraw.Draw(body)
    d_.ellipse((W * .18, H * .08, W * .82, H * .95), fill=color, outline=dark, width=12)        # body
    d_.ellipse((W * .3, H * .5, W * .7, H * .9), fill=light)                                    # belly
    d_.ellipse((W * .2, H * .02, W * .36, H * .2), fill=color, outline=dark, width=10)          # ears
    d_.ellipse((W * .64, H * .02, W * .8, H * .2), fill=color, outline=dark, width=10)
    for ex in (.43, .63):                                                                       # eyes (look right)
        d_.ellipse((W * (ex - .07), H * .25, W * (ex + .07), H * .41), fill="white", outline=dark, width=6)
        d_.ellipse((W * (ex - .01), H * .3, W * (ex + .05), H * .38), fill=(40, 30, 40))
        d_.ellipse((W * (ex + .005), H * .305, W * (ex + .025), H * .325), fill="white")
    d_.ellipse((W * .66, H * .45, W * .74, H * .5), fill=(255, 140, 140, 160))                  # cheek
    body.save(d / "govde.png")
    eyes = base(); e_ = ImageDraw.Draw(eyes)
    for ex in (.43, .63):
        e_.ellipse((W * (ex - .075), H * .245, W * (ex + .075), H * .415), fill=color)
        e_.arc((W * (ex - .06), H * .3, W * (ex + .06), H * .38), 10, 170, fill=dark, width=8)
    eyes.save(d / "goz_kapali.png")
    mx, my = W * .56, H * .5
    shapes = {"X": (.07, .006), "A": (.06, .004), "B": (.07, .025), "C": (.075, .045), "D": (.085, .07),
              "E": (.05, .06), "F": (.035, .04), "G": (.07, .03), "H": (.075, .05)}
    for v, (rw, rh) in shapes.items():
        m = base(); m_ = ImageDraw.Draw(m)
        if rh < .01:
            m_.arc((mx - W * rw, my - H * .03, mx + W * rw, my + H * .03), 20, 160, fill=dark, width=9)
        else:
            m_.ellipse((mx - W * rw, my - H * rh / 2, mx + W * rw, my + H * rh), fill=(150, 50, 60), outline=dark, width=7)
            if rh > .035:
                m_.ellipse((mx - W * rw * .5, my + H * rh * .35, mx + W * rw * .5, my + H * rh * .95), fill=(240, 120, 120))
        m.save(d / f"agiz_{v}.png")
    (d / "kukla.json").write_text(json.dumps({"height": .42}))
    return d
