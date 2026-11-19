"""Numeric experience parsing and the candidate's 4-year ceiling.

The parser is deliberately based on one combined regex so that a range such as
"4-7 years" is read once (4 -> 7), not as "4 years" plus "7 years".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

UNIT = r"(?:years?|yrs?|y/o/e|yoe)"
_EXP = re.compile(
    rf"""
    (?<![\w.])
    (?P<rmin>\d{{1,2}})\s*(?:-|–|—|to|through)\s*(?P<rmax>\d{{1,2}})
    \s*\+?\s*(?P<runit>{UNIT})\b
    |
    (?<![\w.])(?:at\s+least|minimum(?:\s+of)?|min\.?)\s*
    (?P<pmin>\d{{1,2}})\s*(?P<punit>{UNIT})\b
    |
    (?<![\w.])(?P<smin>\d{{1,2}})\s*\+\s*(?P<sunit>{UNIT})\b
    |
    (?<![\w.])(?P<vmin>\d{{1,2}})\s*(?P<vunit>{UNIT})\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class ExperienceRequirement:
    min_years: int
    max_years: int | None
    raw: str
    open_ended: bool = False
    source: str = "description"

    @property
    def label(self) -> str:
        if self.open_ended and self.max_years is not None and self.max_years != self.min_years:
            return f"{self.min_years}-{self.max_years}+ years"
        if self.open_ended:
            return f"{self.min_years}+ years"
        if self.max_years is None or self.max_years == self.min_years:
            return f"{self.min_years} years"
        return f"{self.min_years}-{self.max_years} years"


@dataclass(frozen=True)
class ExperienceDecision:
    ok: bool
    note: str
    requirement: ExperienceRequirement | None = None
    bonus: int = 0
    reason: str = ""


# Company-maturity sentences ("founded 8 years ago", "12 years in business")
# contain a number and a year unit but are not candidate requirements.  Only
# the words immediately after the unit (or "in business for N years") count,
# so "Founded in 2010, we need 5+ years of experience" is still a requirement.
_AGE_MATURITY = re.compile(
    r"\b(?:in\s+business|operating)\s+for\s+"
    r"(?:(?:over|more\s+than)\s+)?$",
    re.IGNORECASE,
)
_AGE_SUFFIX = re.compile(
    r"^\s*(?:ago|old|in\s+business|of\s+operation|"
    r"of\s+company\s+history|of\s+history)\b",
    re.IGNORECASE,
)


def _is_company_age(text: str, start: int, end: int) -> bool:
    prefix = text[max(0, start - 40): start]
    suffix = text[end: end + 40]
    return bool(_AGE_MATURITY.search(prefix) or _AGE_SUFFIX.search(suffix))


def extract_experience(text: str, source: str = "description") -> list[ExperienceRequirement]:
    """Return every numeric experience statement in text."""
    cleaned = re.sub(r"\bplus\b", "+", text or "", flags=re.IGNORECASE)
    found: list[ExperienceRequirement] = []
    seen: set[tuple[int, int | None, bool]] = set()
    for match in _EXP.finditer(cleaned):
        if source != "title" and _is_company_age(cleaned, match.start(), match.end()):
            continue
        if match.group("rmin") is not None:
            lo = int(match.group("rmin"))
            hi = int(match.group("rmax"))
            open_ended = "+" in match.group(0)
        elif match.group("pmin") is not None:
            lo = int(match.group("pmin"))
            hi = None
            open_ended = True
        elif match.group("smin") is not None:
            lo = int(match.group("smin"))
            hi = None
            open_ended = True
        else:
            lo = int(match.group("vmin"))
            hi = lo
            open_ended = False
        key = (lo, hi, open_ended)
        if key in seen:
            continue
        seen.add(key)
        found.append(
            ExperienceRequirement(
                min_years=lo,
                max_years=hi,
                raw=match.group(0).strip(),
                open_ended=open_ended,
                source=source,
            )
        )
    return found


def evaluate_experience(
    requirements: list[ExperienceRequirement],
    max_years: int = 4,
    candidate_years: float = 3.5,
) -> ExperienceDecision:
    """Apply the candidate's ceiling to parsed requirements."""
    if not requirements:
        return ExperienceDecision(
            ok=True,
            note="Experience not stated",
            reason="experience requirements not stated",
        )

    for req in requirements:
        if req.min_years > max_years:
            return ExperienceDecision(
                ok=False,
                note=f"requires {req.label}",
                requirement=req,
                reason=f"requires {req.label} - above the {max_years}-year ceiling",
            )
        # A range that starts at the ceiling and ends well above it is a
        # senior role in disguise ("4-7 years", "4-8 years").
        if (
            req.min_years >= max_years
            and req.max_years is not None
            and req.max_years > max_years + 1
        ):
            return ExperienceDecision(
                ok=False,
                note=f"requires {req.label}",
                requirement=req,
                reason=f"range {req.label} extends well above the {max_years}-year ceiling",
            )
        if req.max_years is not None and req.max_years <= 1:
            return ExperienceDecision(
                ok=False,
                note=f"requires {req.label}",
                requirement=req,
                reason=f"requires {req.label} - below the target experience band",
            )

    best = min(
        requirements,
        key=lambda r: (
            0 if (r.max_years is not None and r.max_years <= max_years) else 1,
            r.min_years,
            r.max_years if r.max_years is not None else 99,
        ),
    )
    bonus = 0
    if best.max_years is not None and best.max_years <= max_years:
        bonus = 4
    elif best.min_years <= 3 and (best.max_years is None or best.max_years <= 5):
        bonus = 2
    if best.min_years >= max_years:
        fit = f"at your {max_years}-year ceiling"
    else:
        fit = f"fits your {candidate_years:g} years"
    return ExperienceDecision(
        ok=True,
        note=f"requires {best.label} ({fit})",
        requirement=best,
        bonus=bonus,
        reason=f"experience fits: {best.label}",
    )
