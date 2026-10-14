import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def make_cfg(**over):
    from studio import config
    base = {"channel": {"name": "Test", "languages": ["tr", "en"], "type": "kids_story"},
            "characters": [{"id": "mavi", "name": {"tr": "Mavi", "en": "Mavi"}, "description": "blue bunny"},
                           {"id": "kivircik", "name": {"tr": "Kıvırcık", "en": "Kivircik"}, "description": "green frog"}],
            "voice": {"provider": "placeholder"}, "visuals": {"mode": "placeholder"},
            "script": {"written_language": "tr"}, "llm": {"provider": "none"}}
    return config.Config(config._merge(config._merge(config.DEFAULTS, base), over))
