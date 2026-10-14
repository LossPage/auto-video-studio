import datetime as dt
from zoneinfo import ZoneInfo

from conftest import make_cfg
from studio import scheduler, seo


def test_tags_limit_and_dedupe():
    tags = seo.clean_tags(["Kids Stories", "kids stories", "#moon"] + [f"long tail phrase number {i}" for i in range(40)], "Chan", [])
    assert tags[0] == "Kids Stories" and "kids stories" not in tags[1:] and "moon" in tags
    total = sum(len(x) + (2 if " " in x else 0) + 1 for x in tags)
    assert total <= 480


def test_title_and_hashtags():
    assert len(seo.clean_title("x" * 50 + " " + "y" * 80)) <= 100
    assert seo.clean_title("<b>Hi</b>") == "bHi/b"
    assert seo.clean_hashtags(["moon facts", "#Kids", "kids"]) == ["#moonfacts", "#Kids"]


def test_chapters_rules():
    chs = [(3.0, "Intro"), (5.0, "Too close"), (40.0, "Middle"), (90.0, "End")]
    txt = seo.chapters_text(chs, 120)
    assert txt.splitlines()[0].startswith("0:00 ")
    assert "Too close" not in txt and len(txt.splitlines()) == 3
    assert seo.chapters_text(chs[:2], 120) == ""


def test_due_jobs_and_publish_slot():
    cfg = make_cfg(schedule={"timezone": "Europe/Istanbul", "produce": [{"days": ["mon", "thu"], "time": "09:00"}],
                             "publish_times": ["17:00"], "publish_days": ["fri"]})
    z = ZoneInfo("Europe/Istanbul")
    mon = dt.datetime(2026, 10, 12, 9, 30, tzinfo=z)          # Monday
    assert [k for k, _ in scheduler.due_jobs(cfg, mon, {})] == ["produce0"]
    assert scheduler.due_jobs(cfg, mon, {"produce0": mon.isoformat()}) == []
    assert scheduler.due_jobs(cfg, dt.datetime(2026, 10, 13, 9, 30, tzinfo=z), {}) == []   # Tuesday: >12 h late, skipped
    slot = scheduler.next_publish(cfg, mon, set())
    assert slot.weekday() == 4 and slot.hour == 17
    assert scheduler.next_publish(cfg, mon, {slot.isoformat()}) > slot
