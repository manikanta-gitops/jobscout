import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from jobscout.details import greenhouse
from jobscout.experience import evaluate_experience, extract_experience
from jobscout.filters import JobFilter, _location_status
from jobscout.main import format_job_with_match
from jobscout.matching import SKILL, assess_location, evaluate, quick_reject
from jobscout.models import Job
from jobscout.sections import split_sections
from jobscout.sources import _channel_location, _dt, _text
from jobscout.store import Store
from jobscout.taxonomy import classify_title

NOW = datetime(2026, 1, 10, tzinfo=timezone.utc)
CFG = {
    "include": ["devops", "sre", "platform engineer", "cloud engineer", "cloud infrastructure"],
    "exclude_title": ["intern", "sales", "recruiter", "senior", "lead"],
    "locations": ["india", "unverified"],
    "max_age_days": 7,
}
MATCH_CFG = {"send_score": 70, "review_score": 40, "max_experience_years": 4}

STRONG_DESC = (
    "We are looking for a DevOps Engineer with 2-4 years of experience.\n\n"
    "Requirements:\n"
    "- AWS (EKS, ECR, S3, CloudWatch, IAM)\n"
    "- Kubernetes, Helm, Docker\n"
    "- Terraform\n"
    "- GitHub Actions CI/CD\n"
    "- GitOps with ArgoCD\n"
    "- Datadog observability\n\n"
    "Preferred:\n"
    "- Python\n"
    "- Snyk\n"
)


def job(**kw):
    base = dict(
        source="t",
        id="1",
        title="DevOps Engineer",
        company="Acme",
        url="https://x",
        location="Hyderabad, India",
    )
    return Job(**{**base, **kw})


class ExperienceTests(unittest.TestCase):
    def test_ranges_are_parsed_once(self):
        cases = [
            ("1-3 years", 1, 3, False),
            ("2-4 yrs", 2, 4, False),
            ("4 years", 4, 4, False),
            ("5+ years", 5, None, True),
            ("4-7 years", 4, 7, False),
            ("6+ yrs", 6, None, True),
            ("1 to 4 years", 1, 4, False),
            ("0-4 years", 0, 4, False),
            ("at least 5 years", 5, None, True),
        ]
        for text, low, high, open_ended in cases:
            with self.subTest(text=text):
                reqs = extract_experience(text)
                self.assertEqual(len(reqs), 1)
                self.assertEqual(reqs[0].min_years, low)
                self.assertEqual(reqs[0].max_years, high)
                self.assertEqual(reqs[0].open_ended, open_ended)

    def test_accepts_within_ceiling(self):
        for text in ["0-4 years", "1-3 years", "2-4 years", "3-5 years", "4 years", "3+ years"]:
            with self.subTest(text=text):
                self.assertTrue(evaluate_experience(extract_experience(text)).ok)

    def test_rejects_above_ceiling(self):
        for text in ["5+ years", "6+ years", "7+ years", "8+ years", "4-7 years", "4-8 years"]:
            with self.subTest(text=text):
                self.assertFalse(evaluate_experience(extract_experience(text)).ok)

    def test_unspecified_is_allowed(self):
        decision = evaluate_experience([])
        self.assertTrue(decision.ok)
        self.assertIn("not stated", decision.note.lower())

    def test_company_age_is_not_a_requirement(self):
        for text in [
            "Our company was founded 8 years ago.",
            "We have been in business for over 12 years.",
            "Established in 2016, 10 years ago.",
            "12 years in business.",
        ]:
            with self.subTest(text=text):
                self.assertEqual(extract_experience(text), [])

        # A real requirement in the same or next sentence must still be parsed.
        reqs = extract_experience("Founded 8 years ago. You need 2-4 years of experience.")
        self.assertEqual([(r.min_years, r.max_years) for r in reqs], [(2, 4)])
        reqs = extract_experience("Founded in 2010, we need 5+ years of experience.")
        self.assertEqual([(r.min_years, r.max_years) for r in reqs], [(5, None)])
        self.assertFalse(evaluate_experience(reqs).ok)


class TitleTests(unittest.TestCase):
    def test_relevant_titles(self):
        for title in [
            "DevOps Engineer",
            "Site Reliability Engineer",
            "Platform Engineer",
            "Cloud Infrastructure Engineer",
            "Infrastructure Software Engineer",
            "AWS DevOps Engineer",
            "Kubernetes Engineer",
            "Production Engineer",
            "Release Engineer",
            "Systems Administrator",
            "Cloud Software Engineer",
            "DevOps Engineer - Data Platform",
            "Platform Engineer, Data",
            "Data Platform Engineer",
            "Data Infrastructure Engineer",
            "Analytics Platform Engineer",
            "ML Infrastructure Engineer",
            "Mobile Platform Engineer",
        ]:
            with self.subTest(title=title):
                self.assertIsNone(classify_title(title).rejection)

    def test_rejected_titles(self):
        for title in [
            "Senior DevOps Engineer",
            "Staff Platform Engineer",
            "Lead SRE",
            "Principal SRE",
            "DevOps Architect",
            "DevOps Manager",
            "Engineering Manager, Infrastructure",
            "DevOps Intern",
            "Junior DevOps Engineer",
            "Jr. DevOps Engineer",
            "Test Platform Engineer",
            "Salesforce Platform Engineer",
            "Frontend Platform Engineer",
            "iOS Engineer",
            "Data Scientist",
            "QA Automation Engineer",
            "Java Developer",
            "Backend Engineer",
            "Software Engineer",
        ]:
            with self.subTest(title=title):
                self.assertIsNotNone(classify_title(title).rejection)


class SectionTests(unittest.TestCase):
    def test_required_and_preferred(self):
        sections = split_sections(
            "We need 3+ years.\nRequirements:\nAWS, Kubernetes\nNice to have:\nPython"
        )
        self.assertIn("3+ years", sections.required)
        self.assertIn("AWS", sections.required)
        self.assertIn("Python", sections.preferred)

    def test_preferred_location_does_not_split_requirements(self):
        sections = split_sections(
            "Preferred location: Hyderabad.\nRequired: AWS, Kubernetes\n3+ years of experience."
        )
        self.assertIn("AWS", sections.required)
        self.assertIn("3+ years", sections.required)

    def test_benefits_section_does_not_pollute_required(self):
        sections = split_sections(
            "Requirements:\nAWS, Kubernetes\nBenefits:\n5+ years of company history and free lunch"
        )
        self.assertIn("AWS", sections.required)
        self.assertNotIn("5+ years", sections.required)


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.f = JobFilter(CFG)

    def test_core_title_match(self):
        self.assertTrue(self.f.accepts(job(), NOW))
        self.assertTrue(self.f.accepts(job(title="Platform Engineer"), NOW))
        self.assertTrue(self.f.accepts(job(title="Cloud Infrastructure Engineer"), NOW))

    def test_skill_tag_does_not_admit_unrelated_title(self):
        self.assertFalse(self.f.accepts(job(title="Software Engineer", tags=["kubernetes"]), NOW))
        self.assertFalse(self.f.accepts(job(title="Mobile Developer", tags=["devops"]), NOW))

    def test_exclude_and_seniority(self):
        self.assertFalse(self.f.accepts(job(title="DevOps Intern"), NOW))
        self.assertFalse(self.f.accepts(job(title="Senior DevOps Engineer"), NOW))
        self.assertFalse(self.f.accepts(job(title="Sresta Manager"), NOW))

    def test_location_india_verified(self):
        self.assertTrue(self.f.accepts(job(location="Hyderabad, India"), NOW))
        self.assertTrue(self.f.accepts(job(location="Bangalore"), NOW))
        self.assertTrue(self.f.accepts(job(location="Remote - India"), NOW))

    def test_location_unverified_accepted(self):
        self.assertTrue(self.f.accepts(job(location="Remote"), NOW))
        self.assertTrue(self.f.accepts(job(location="Worldwide"), NOW))
        self.assertTrue(self.f.accepts(job(location="Anywhere"), NOW))
        self.assertTrue(self.f.accepts(job(location="APAC"), NOW))
        self.assertTrue(self.f.accepts(job(location=""), NOW))

    def test_location_outside_rejected(self):
        self.assertFalse(self.f.accepts(job(location="United States"), NOW))
        self.assertFalse(self.f.accepts(job(location="London, UK"), NOW))
        self.assertFalse(self.f.accepts(job(location="Indianapolis, IN"), NOW))

    def test_channels_legacy_location_aliases(self):
        f = JobFilter({"include": [], "locations": ["india", "remote", "bangalore"], "max_age_days": 2})
        self.assertTrue(f.accepts(job(location=""), NOW))  # unverified via "remote"
        self.assertTrue(f.accepts(job(location="Hyderabad"), NOW))
        self.assertFalse(f.accepts(job(location="United States"), NOW))

    def test_age(self):
        self.assertFalse(self.f.accepts(job(posted_at=NOW - timedelta(days=8)), NOW))
        self.assertTrue(self.f.accepts(job(posted_at=NOW - timedelta(days=1)), NOW))

    def test_location_status_helper(self):
        self.assertEqual(_location_status("Hyderabad, India"), "india")
        self.assertEqual(_location_status("Remote"), "unverified")
        self.assertEqual(_location_status("APAC"), "unverified")
        self.assertEqual(_location_status("United States"), "outside_india")
        self.assertEqual(_location_status("Indianapolis, IN"), "outside_india")
        self.assertEqual(_location_status(""), "unverified")


class MatchingTests(unittest.TestCase):
    def test_strong_match_sends(self):
        result = evaluate(job(description=STRONG_DESC), MATCH_CFG)
        self.assertEqual(result.verdict, "send")
        self.assertGreaterEqual(result.score, 70)
        self.assertEqual(result.confidence, "high")
        self.assertIn("2-4 years", result.experience_note)
        for name in ["AWS", "Kubernetes/EKS", "Terraform", "Docker", "Helm", "CI/CD", "GitOps/ArgoCD", "Observability"]:
            self.assertIn(name, result.matched_must)

    def test_experience_edges_affect_verdict(self):
        for text, verdict in [
            ("1-3 years", "send"),
            ("2-4 years", "send"),
            ("4 years", "send"),
            ("5+ years", "reject"),
            ("4-7 years", "reject"),
            ("6+ years", "reject"),
        ]:
            with self.subTest(text=text):
                desc = STRONG_DESC.replace("2-4 years", text)
                self.assertEqual(evaluate(job(description=desc), MATCH_CFG).verdict, verdict)

    def test_senior_title_rejected_even_with_three_years(self):
        result = evaluate(job(title="Senior DevOps Engineer", description=STRONG_DESC), MATCH_CFG)
        self.assertEqual(result.verdict, "reject")
        self.assertIn("senior", " ".join(result.reasons).lower())

    def test_unrelated_domains_rejected(self):
        for title in [
            "Data Scientist",
            "Data Engineer",
            "ML Engineer",
            "Frontend Engineer",
            "Mobile Developer",
            "QA Automation Engineer",
            "Test Platform Engineer",
            "Backend Engineer",
            "Salesforce Platform Engineer",
        ]:
            with self.subTest(title=title):
                self.assertEqual(evaluate(job(title=title, description=STRONG_DESC), MATCH_CFG).verdict, "reject")

    def test_explicit_devops_role_supporting_data_is_allowed(self):
        result = evaluate(job(title="DevOps Engineer - Data Platform", description=STRONG_DESC), MATCH_CFG)
        self.assertEqual(result.verdict, "send")

    def test_preferred_skill_is_not_a_matched_core_skill(self):
        desc = (
            "We need a DevOps Engineer with 2-4 years of experience.\n\n"
            "Requirements:\n- Kubernetes\n- Terraform\n- Docker\n- GitHub Actions CI/CD\n"
            "- Helm\n- GitOps with ArgoCD\n\n"
            "You will own production infrastructure, deployments and on-call.\n\n"
            "Preferred:\n- AWS\n- Datadog\n"
        )
        result = evaluate(job(description=desc), MATCH_CFG)
        self.assertIn("Kubernetes/EKS", result.matched_must)
        self.assertNotIn("AWS", result.matched_must)

    def test_remote_role_can_send(self):
        result = evaluate(job(location="Remote", description=STRONG_DESC), MATCH_CFG)
        self.assertEqual(result.verdict, "send")
        self.assertEqual(result.location_status, "unverified")

    def test_short_description_is_scored_but_capped(self):
        result = evaluate(job(description="AWS, Kubernetes, Terraform, Helm, CI/CD"), MATCH_CFG)
        self.assertNotEqual(result.verdict, "send")
        self.assertLessEqual(result.score, 69)
        self.assertIn("AWS", result.matched_must)
        self.assertIn("short description", " ".join(result.reasons))

    def test_missing_description_uses_title_and_tags_only(self):
        result = evaluate(job(title="AWS DevOps Engineer", tags=["kubernetes"]), MATCH_CFG)
        self.assertNotEqual(result.verdict, "send")
        self.assertLessEqual(result.score, 69)
        self.assertIn("no description", " ".join(result.reasons))

    def test_quick_reject(self):
        self.assertIsNone(quick_reject(job(title="DevOps Engineer")))
        self.assertIsNotNone(quick_reject(job(title="Senior DevOps Engineer")))
        self.assertIsNotNone(quick_reject(job(title="Data Engineer")))
        self.assertIsNotNone(quick_reject(job(title="Test Platform Engineer")))
        self.assertIsNotNone(quick_reject(job(title="DevOps Engineer (5+ years)")))

    def test_skill_substring_false_positives(self):
        patterns = dict(SKILL)
        self.assertFalse(patterns["AWS"].search("the laws, flaws, draws and lawsuit"))
        self.assertFalse(patterns["Docker"].search("shipping containers"))

    def test_missing_empty_ambiguous_location(self):
        short = "AWS, Terraform, Kubernetes, Helm, CI/CD, 3+ years exp."
        self.assertEqual(evaluate(job(location="", description=short), MATCH_CFG).location_status, "unverified")
        self.assertEqual(evaluate(job(location="Remote - US/India", description=short), MATCH_CFG).location_status, "india")
        self.assertEqual(evaluate(job(location="APAC", description=short), MATCH_CFG).location_status, "unverified")
        self.assertEqual(evaluate(job(location="United States", description=STRONG_DESC), MATCH_CFG).location_status, "outside_india")
        self.assertEqual(assess_location("Indianapolis, IN")[0], "outside_india")


class TelegramFormatTests(unittest.TestCase):
    def test_message_contains_match_details(self):
        result = evaluate(job(description=STRONG_DESC), MATCH_CFG)
        message = format_job_with_match(job(description=STRONG_DESC), result)
        self.assertIn("DevOps Engineer", message)
        self.assertIn("Acme", message)
        self.assertIn("Hyderabad, India", message)
        self.assertIn("2-4 years", message)
        self.assertIn("Core skills:", message)
        self.assertIn("Terraform", message)
        self.assertIn(f"{result.score}/100", message)
        self.assertIn("Apply", message)

    def test_message_escapes_html_and_handles_remote(self):
        result = evaluate(job(title="DevOps Engineer", location="", description=STRONG_DESC), MATCH_CFG)
        message = format_job_with_match(
            job(title="<DevOps & SRE>", company="<Acme>", location="", description=STRONG_DESC),
            result,
        )
        self.assertIn("&lt;DevOps &amp; SRE&gt;", message)
        self.assertIn("&lt;Acme&gt;", message)
        self.assertIn("Remote / not specified", message)
        self.assertIn("&amp;", message)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    def __init__(self, payload):
        self._payload = payload

    def get(self, *args, **kwargs):
        return _Response(self._payload)


class DetailTests(unittest.TestCase):
    def test_greenhouse_html_escaped_content_is_parsed(self):
        payload = {
            "content": (
                "&lt;p&gt;Requirements:&lt;/p&gt;"
                "&lt;li&gt;AWS&lt;/li&gt;&lt;li&gt;Kubernetes&lt;/li&gt;"
            )
        }
        text = greenhouse(_Session(payload), job(source="greenhouse", id="acme-1"))
        self.assertIn("Requirements:", text)
        self.assertIn("AWS", text)
        self.assertIn("Kubernetes", text)
        self.assertNotIn("&lt;", text)


class StoreAndSourceTests(unittest.TestCase):
    def test_store_dedupes_same_source_and_same_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(f"{tmp}/state.json")
            first = job(source="x", id="a")
            store.add(first, NOW)
            self.assertTrue(store.has(first))
            self.assertTrue(store.has(job(source="x", id="b")))
            self.assertFalse(store.has(job(source="x", id="c", location="Pune, India")))

    def test_datetime_parsing(self):
        self.assertEqual(_dt("2026-01-10T00:00:00Z").year, 2026)
        self.assertEqual(_dt(1768000000000).tzinfo, timezone.utc)

    def test_text_keeps_newlines_for_descriptions(self):
        raw = "<p>AWS</p><ul><li>Kubernetes</li></ul>"
        self.assertIn("\n", _text(raw, keep_newlines=True))
        self.assertNotIn("\n", _text(raw))

    def test_channel_location_line(self):
        self.assertEqual(_channel_location("Hiring DevOps\nLocation: Hyderabad\nApply"), "Hyderabad")
        self.assertEqual(_channel_location("Work Location - Remote"), "Remote")
        self.assertEqual(_channel_location("No location line"), "")


if __name__ == "__main__":
    unittest.main()
