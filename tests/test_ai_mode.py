"""AI and idea modes with a fake model: the pipeline must accept any schema-shaped answer and keep the idea queue."""
from conftest import make_cfg
from studio import llm, pipeline, script as S


def fake_episode(*_a, **_k):
    L = lambda tr, en: {"tr": tr, "en": en}
    return {"title": L("Yağmur Nereden Gelir?", "Where Does Rain Come From?"), "summary": L("Özet", "Summary"),
            "thumbnail_text": L("YAĞMUR!", "RAIN!"), "topic_keywords": ["rain for kids"],
            "scenes": [{"id": "x", "title": L("Bulut", "Cloud"), "location": "sky",
                        "shots": [{"id": "y", "visual": "clouds", "characters": ["mavi", "ghost"], "camera": "dolly",
                                   "lines": [{"speaker": "mavi", "emotion": "curious", "text": L("Bak, bulut!", "Look, a cloud!")},
                                             {"speaker": "unknown", "emotion": "", "text": L("Yağmur geliyor.", "")}],
                                   "pause_after": 1}]}],
            "shorts": [{"title": L("Kısa", "Short"), "shot_ids": ["S1_01", "nope"]}]}


def test_idea_mode_creates_episode(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_CHANNEL_DIR", str(tmp_path / "kanal"))
    monkeypatch.setenv("STUDIO_OUTPUT_DIR", str(tmp_path / "cikti"))
    monkeypatch.setattr(llm, "generate", fake_episode)
    cfg = make_cfg(script={"mode": "idea", "approval": True})
    S.ideas_file().parent.mkdir(parents=True)
    S.ideas_file().write_text("# Fikirler\n\n- yağmur nasıl oluşur\n- gökkuşağı\n\n## Kullanılanlar\n", encoding="utf-8")
    eid = pipeline.create(cfg)
    st = pipeline.state(eid)
    assert st["stage"] == "script_review" and st["source"]["idea"] == "yağmur nasıl oluşur"
    assert S.pending_ideas() == ["gökkuşağı"]
    sc = pipeline.read_json(pipeline.ep_dir(eid) / "senaryo.json")
    sh = sc["scenes"][0]["shots"][0]
    assert sh["camera"] in S.CAMERAS and sh["characters"] == ["mavi"]
    assert sh["lines"][1]["speaker"] == "narrator" and sh["lines"][1]["text"]["en"] == "Yağmur geliyor."
    assert sc["shorts"] == [{"title": {"tr": "Kısa", "en": "Short"}, "shot_ids": ["S1_01"]}]
    assert pipeline.approve(cfg, eid) == "visuals"
