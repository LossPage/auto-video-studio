"""The step-by-step setup with a fake AI: questions → concept (revised once) → cast → plan → first episode."""
import yaml

from studio import config, llm, util, wizard
from test_ai_mode import fake_episode


def test_ai_guided_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_CHANNEL_DIR", str(tmp_path / "kanal"))
    monkeypatch.setenv("STUDIO_OUTPUT_DIR", str(tmp_path / "cikti"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(util, "ROOT", tmp_path)
    monkeypatch.setattr(wizard, "ROOT", tmp_path)
    calls = []

    def fake(cfg, system, prompt, schema=None, max_tokens=0):
        calls.append((schema, prompt))
        if schema is wizard.QUESTIONS_SCHEMA:
            return {"questions": [{"question": "Kaç yaş?", "example": "4-7"}, {"question": "Ton?", "example": "komik"}]}
        if schema is wizard.CONCEPT_SCHEMA:
            funny = "daha komik" in prompt
            return {"name_suggestions": ["Neden Ormanı", "Mavi'nin Soruları"], "concept": "Komik doğa soruları" if funny else "Doğa soruları",
                    "audience": "4-7 yaş", "tone": "komik" if funny else "sıcak", "made_for_kids": True, "target_minutes": 6,
                    "episode_formula": "Soru, macera, cevap.", "writing_rules": ["Kısa cümleler"], "keywords": ["kids science"],
                    "hashtags": ["#neden"]}
        if schema is wizard.CAST_SCHEMA:
            return {"visual_style": "soft 2D cartoon", "narrator_enabled": True, "narrator_voice_style": "warm woman",
                    "characters": [{"name": "Mavi", "description": "blue bunny", "personality": "Meraklı.", "voice_style": "young girl"},
                                   {"name": "Kıvırcık", "description": "green frog", "personality": "Komik.", "voice_style": "young boy"}]}
        if schema is wizard.PLAN_SCHEMA:
            return {"episodes": [{"title": "Yağmur", "idea": "Yağmur nereden gelir?"}, {"title": "Gökkuşağı", "idea": "Renkler nereden?"}]}
        return fake_episode()

    monkeypatch.setattr(llm, "generate", fake)
    answers = dict(ui_language="tr", languages=["tr", "en"], llm_provider="anthropic", anthropic_key="test-key",
                   type="kids_story", concept="Tavşan ve kurbağa doğayı keşfeder", concept_q1="5 yaş",
                   concept_ok="daha komik olsun", visual_mode="placeholder", plan_count=2, voice_provider="placeholder",
                   test_video=False)
    f = tmp_path / "cevaplar.yaml"
    f.write_text(yaml.safe_dump(answers, allow_unicode=True), encoding="utf-8")
    wizard.run(str(f))

    cfg = config.load()
    assert cfg["channel"]["name"] == "Neden Ormanı" and cfg["channel"]["concept"] == "Komik doğa soruları"   # revised draft kept
    assert sum(s is wizard.CONCEPT_SCHEMA for s, _ in calls) == 2
    assert "Q: Kaç yaş?\nA: 5 yaş" in next(p for s, p in calls if s is wizard.CONCEPT_SCHEMA)
    assert [c["id"] for c in cfg["characters"]] == ["mavi", "kivircik"] and cfg["visuals"]["style"] == "soft 2D cartoon"
    assert cfg["script"]["mode"] == "idea" and cfg["format"]["target_minutes"] == 6
    assert (tmp_path / ".env").read_text().count("ANTHROPIC_API_KEY=test-key") == 1
    bible = (tmp_path / "kanal" / "KANAL.md").read_text(encoding="utf-8")
    assert "Soru, macera, cevap." in bible and "Meraklı." in bible
    ideas = (tmp_path / "kanal" / "icerik" / "fikirler.md").read_text(encoding="utf-8")
    assert "- Yağmur: Yağmur nereden gelir?" in ideas
    assert "Yağmur Nereden Gelir?" in (tmp_path / "kanal" / "icerik" / "ilk-bolum.md").read_text(encoding="utf-8")
    assert not (tmp_path / "kanal" / ".kurulum.json").exists()
