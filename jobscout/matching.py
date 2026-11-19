"""Relevance scoring against the candidate profile.

Nothing here invents facts: when a description cannot be fetched the match is
capped at REVIEW confidence and labelled.  Experience is parsed numerically
against a 4-year ceiling, and title classification rejects senior, entry and
unrelated-role titles before the scorer runs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from . import profile
from .experience import extract_experience, evaluate_experience
from .models import Job
from .sections import split_sections
from .taxonomy import classify_title

log = logging.getLogger("jobscout")

SKILL = profile._compile_group(profile.MUST_HAVE)
SUPPORT = profile._compile_group(profile.SUPPORTING)

ANCHOR_SKILLS = {"AWS", "Kubernetes/EKS", "Terraform", "CI/CD"}
MIN_DESC_LEN = 200
SEND_SCORE = 70

_DUTY = re.compile(
    r"\b(?:deploy(?:ment|ing)?|infrastructure|kubernetes|terraform|ci\s*/\s*cd|"
    r"pipeline|cluster|on[- ]call|observability|monitoring|cloud|platform|"
    r"reliability|gitops|argocd|docker|helm)\b",
    re.IGNORECASE,
)


@dataclass
class MatchResult:
    verdict: str  # "send" | "review" | "reject"
    score: int
    confidence: str  # "high" | "medium" | "low"
    location_status: str  # "india" | "unverified" | "outside_india"
    location_note: str
    matched_must: list[str] = field(default_factory=list)
    matched_support: list[str] = field(default_factory=list)
    missing_must: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    experience_note: str = "Experience not stated"
    experience_min: int | None = None
    experience_max: int | None = None
    role_family: str = ""


def _title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").lower())


def _normalize_text(text: str) -> str:
    text = (text or "").lower().replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def assess_location(raw: str) -> tuple[str, str]:
    """Structured check. Word boundaries, so 'Indianapolis' never matches 'india'."""
    loc = re.sub(r"\s+", " ", (raw or "").strip())
    if not loc:
        return "unverified", "no location listed"
    if re.search(profile.INDIA_TOKEN, loc, re.IGNORECASE):
        return "india", loc
    if re.search(profile.UNVERIFIED_TOKEN, loc, re.IGNORECASE):
        return "unverified", f"employer says '{loc}' - country unclear"
    return "outside_india", f"{loc} (outside India)"


def quick_reject(job: Job) -> str | None:
    """Pre-fetch screen for titles that cannot match, plus title experience."""
    info = classify_title(job.title)
    if info.rejection:
        return info.rejection
    title_experience = extract_experience(job.title, source="title")
    if title_experience:
        decision = evaluate_experience(
            title_experience,
            max_years=profile.MAX_YEARS_EXPERIENCE,
            candidate_years=profile.YEARS_EXPERIENCE,
        )
        if not decision.ok:
            return decision.reason
    return None


def _score_skills(
    required_text: str,
    preferred_text: str,
    all_text: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Return core hits, core-preferred hits, missing core, supporting hits."""
    must_hits: list[str] = []
    weak_hits: list[str] = []
    missing: list[str] = []
    sup_hits: list[str] = []

    for name, pattern in SKILL:
        if pattern.search(required_text):
            must_hits.append(name)
        elif pattern.search(preferred_text) or pattern.search(all_text):
            weak_hits.append(name)
        else:
            missing.append(name)

    for name, pattern in SUPPORT:
        if pattern.search(all_text):
            sup_hits.append(name)

    return must_hits, weak_hits, missing, sup_hits


def evaluate(job: Job, match_cfg: dict) -> MatchResult:
    send_at = int(match_cfg.get("send_score", SEND_SCORE))
    review_at = int(match_cfg.get("review_score", 40))
    max_years = int(match_cfg.get("max_experience_years", profile.MAX_YEARS_EXPERIENCE))

    loc_status, loc_note = assess_location(job.location)
    info = classify_title(job.title)

    if info.rejection:
        return MatchResult(
            "reject",
            0,
            "low",
            loc_status,
            loc_note,
            reasons=[info.rejection],
            role_family=info.role_family or "",
        )

    desc = (job.description or "").strip()
    text = _normalize_text(desc)
    verified = len(text) >= MIN_DESC_LEN
    sections = split_sections(text)
    required_text = sections.required or text
    preferred_text = sections.preferred

    # Experience is checked from the title and from the required description
    # section.  A statement in "nice to have" never rejects a job.
    requirements = extract_experience(job.title, source="title")
    requirements += extract_experience(required_text, source="description")
    decision = evaluate_experience(
        requirements,
        max_years=max_years,
        candidate_years=profile.YEARS_EXPERIENCE,
    )
    if not decision.ok:
        return MatchResult(
            "reject",
            0,
            "low",
            loc_status,
            loc_note,
            reasons=[decision.reason],
            experience_note=decision.note,
            experience_min=decision.requirement.min_years if decision.requirement else None,
            experience_max=decision.requirement.max_years if decision.requirement else None,
            role_family=info.role_family,
        )

    if text:
        # Score whatever text exists.  A short recruiter post is still real
        # evidence; the cap below marks it as lower confidence.
        must_hits, weak_hits, missing, sup_hits = _score_skills(
            required_text, preferred_text, text
        )
    else:
        # Tags are weak evidence; only title matches count as core.
        title_blob = _title(job.title)
        tag_blob = " ".join(t.lower() for t in job.tags)
        must_hits, weak_hits, missing, sup_hits = [], [], [], []
        for name, pattern in SKILL:
            if pattern.search(title_blob):
                must_hits.append(name)
            elif pattern.search(tag_blob):
                weak_hits.append(name)
        for name, pattern in SUPPORT:
            if pattern.search(tag_blob):
                sup_hits.append(name)

    reasons: list[str] = []
    if info.role_family:
        reasons.append(f"{info.role_family} role family matches the profile")
    if info.qualifier:
        reasons.append(f"supports the {info.qualifier} domain")

    if must_hits:
        reasons.append(f"required core skills: {', '.join(must_hits)}")
    if weak_hits:
        reasons.append(f"only preferred/incidental mention of: {', '.join(weak_hits)}")
    if missing:
        reasons.append(f"core skills absent from listing: {', '.join(missing)}")
    if sup_hits:
        reasons.append(f"supporting skills: {', '.join(sup_hits)}")

    duty_terms = {m.group(0).lower() for m in _DUTY.finditer(required_text)}
    duty_pts = min(8, 2 * len(duty_terms))

    title_pts = 30 - (3 if info.qualifier else 0)
    core_pts = min(50, sum(profile.CORE_WEIGHTS.get(name, 6) for name in must_hits))
    weak_pts = min(8, 3 * len(weak_hits))
    sup_pts = min(10, 2 * len(sup_hits))

    score = title_pts + core_pts + weak_pts + sup_pts + duty_pts + decision.bonus
    if missing:
        score -= min(20, 4 * len(missing))

    anchors = [name for name in must_hits if name in ANCHOR_SKILLS]
    reasons.append(decision.reason)

    caps = 100
    if not text:
        caps = min(caps, send_at - 1)
        reasons.append("no description - scored on title/tags only (weak evidence)")
    elif not verified:
        caps = min(caps, send_at - 1)
        reasons.append("short description - limited evidence, not enough to send")
    if len(must_hits) < 3 or len(anchors) < 2:
        caps = min(caps, send_at - 1)
        reasons.append("core stack evidence is too thin for a strong match")

    if loc_status == "unverified":
        # Remote/worldwide is explicitly accepted by the default config.  Keep
        # a penalty for uncertainty but do not make it impossible to send.
        score -= 15
        reasons.append(f"location unverified/remote: {loc_note}")
    elif loc_status == "outside_india":
        score -= 35
        caps = min(caps, review_at)
        reasons.append(f"location outside India - relocation preference unknown: {loc_note}")

    score = max(0, min(int(score), caps, 100))

    if score >= send_at:
        verdict, confidence = "send", "high"
    elif score >= review_at:
        verdict = "review"
        confidence = "medium" if score >= 55 else "low"
    else:
        verdict, confidence = "reject", "low"

    req = decision.requirement
    return MatchResult(
        verdict,
        score,
        confidence,
        loc_status,
        loc_note,
        matched_must=must_hits,
        matched_support=sup_hits,
        missing_must=missing,
        reasons=reasons,
        experience_note=decision.note,
        experience_min=req.min_years if req else None,
        experience_max=req.max_years if req else None,
        role_family=info.role_family or "",
    )
