import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from jobscout.filters import JobFilter
from jobscout.models import Job
from jobscout.notify import format_job
from jobscout.sources import _dt
from jobscout.store import Store

NOW = datetime(2026, 1, 10, tzinfo=timezone.utc)
CFG = {
    "include": ["devops", "sre"],
    "exclude_title": ["intern"],
    "locations": ["india", "worldwide"],
    "max_age_days": 7,
}


def job(**kw):
    base = dict(source="t", id="1", title="DevOps Engineer", company="Acme", url="https://x", location="India")
    return Job(**{**base, **kw})


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.f = JobFilter(CFG)

    def test_title_match(self):
        self.assertTrue(self.f.accepts(job(), NOW))
        self.assertTrue(self.f.accepts(job(title="Senior SRE"), NOW))
        self.assertFalse(self.f.accepts(job(title="Java Developer"), NOW))

    def test_tag_match(self):
        self.assertTrue(self.f.accepts(job(title="Engineer", tags=["devops"]), NOW))

    def test_word_boundary(self):
        self.assertFalse(self.f.accepts(job(title="Sresta Manager"), NOW))

    def test_exclude(self):
        self.assertFalse(self.f.accepts(job(title="DevOps Intern"), NOW))

    def test_location(self):
        self.assertFalse(self.f.accepts(job(location="United States"), NOW))
        self.assertTrue(self.f.accepts(job(location="Worldwide"), NOW))
        self.assertTrue(self.f.accepts(job(location=""), NOW))

    def test_age(self):
        self.assertFalse(self.f.accepts(job(posted_at=NOW - timedelta(days=8)), NOW))
        self.assertTrue(self.f.accepts(job(posted_at=NOW - timedelta(days=1)), NOW))


class StoreTests(unittest.TestCase):
    def test_roundtrip_and_cross_source_dedupe(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(f"{d}/state.json")
            self.assertTrue(s.is_empty)
            s.add(job(source="a"), NOW)
            s.mark_run("a", NOW, ok=True)
            s.save(NOW)
            s2 = Store(f"{d}/state.json")
            self.assertFalse(s2.is_empty)
            self.assertTrue(s2.has(job(source="b", id="99", title="devops  engineer!", company="ACME")))
            self.assertTrue(s2.has(job(source="a")))
            # Same title at the same company on the same board is a different posting.
            self.assertFalse(s2.has(job(source="a", id="2")))
            self.assertFalse(s2.is_due("a", 30, NOW + timedelta(minutes=10)))
            self.assertTrue(s2.is_due("a", 30, NOW + timedelta(minutes=31)))

    def test_failure_counter(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(f"{d}/state.json")
            self.assertEqual(s.mark_run("a", NOW, ok=False), 1)
            self.assertEqual(s.mark_run("a", NOW, ok=False), 2)
            self.assertEqual(s.mark_run("a", NOW, ok=True), 0)


class MiscTests(unittest.TestCase):
    def test_dt(self):
        self.assertEqual(_dt(1767225600), datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(_dt(1767225600000), datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(_dt("2026-01-01T00:00:00Z"), datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertIsNotNone(_dt("Thu, 01 Jan 2026 00:00:00 +0000"))
        self.assertIsNone(_dt("garbage"))

    def test_format_escapes_html(self):
        text = format_job(job(title="<b>x</b> & y"))
        self.assertIn("&lt;b&gt;x&lt;/b&gt; &amp; y", text)


if __name__ == "__main__":
    unittest.main()
