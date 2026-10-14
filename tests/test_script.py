from conftest import make_cfg
from studio import script as S

MD = """<!-- yorum
# Bu bir başlık değil
-->
# Ay Neden Bizi Takip Ediyor?
> en: Why Does the Moon Follow Us?

## Gece | forest path at night
[görsel: Mavi looks up at the moon]
MAVİ (şaşkın): Bak! Ay da bizimle yürüyor!
> en: Look! The moon is walking with us!
KIVIRCIK: Biz durunca o da duruyor mu?
> en: When we stop, does it stop too?
(bekle 2)
ANLATICI: Ay çok uzakta.
> en: The moon is very far away.
"""


def test_parse_markdown_languages_and_speakers():
    cfg = make_cfg()
    s = S.parse_markdown(MD, cfg)
    assert s["title"] == {"tr": "Ay Neden Bizi Takip Ediyor?", "en": "Why Does the Moon Follow Us?"}
    lines = [l for sc in s["scenes"] for sh in sc["shots"] for l in sh["lines"]]
    assert [l["speaker"] for l in lines] == ["mavi", "kivircik", "narrator"]
    assert lines[1]["text"]["en"] == "When we stop, does it stop too?"
    assert s["scenes"][0]["shots"][0]["pause_after"] == 2
    assert s["_missing_langs"] == []


def test_normalize_and_roundtrip():
    cfg = make_cfg()
    s = S.parse_markdown(MD, cfg)
    s.pop("_missing_langs"); s.pop("_written")
    s = S.normalize(s, cfg, fill_from="tr")
    assert s["scenes"][0]["shots"][0]["id"] == "S1_01"
    assert s["scenes"][0]["shots"][0]["characters"] == ["mavi", "kivircik"]
    assert s["shorts"], "a Short is picked automatically without AI"
    md = S.to_markdown(s, make_cfg(channel={"languages": ["tr", "en"]}))
    again = S.parse_markdown(md, cfg)
    assert [l["text"] for sc in again["scenes"] for sh in sc["shots"] for l in sh["lines"]] == \
           [l["text"] for sc in s["scenes"] for sh in sc["shots"] for l in sh["lines"]]


def test_missing_translation_detected():
    cfg = make_cfg()
    s = S.parse_markdown("# Başlık\n## Sahne\nMAVİ: Merhaba!\n", cfg)
    assert s["_missing_langs"] == ["en"]


def test_schema_uses_channel_languages_and_speakers():
    sch = S.schema(make_cfg())
    line = sch["properties"]["scenes"]["items"]["properties"]["shots"]["items"]["properties"]["lines"]["items"]
    assert set(line["properties"]["text"]["required"]) == {"tr", "en"}
    assert "narrator" in line["properties"]["speaker"]["enum"]
