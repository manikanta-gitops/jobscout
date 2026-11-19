"""Structured title classification.

A title is not relevant just because it contains "platform engineer".  This
module normalises the title and separates seniority, unrelated role families
and the infrastructure role family before the scorer sees the job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PUNCT = re.compile(r"[^a-z0-9+#./]+")
_SPACE = re.compile(r"\s+")

SENIORITY = re.compile(
    r"\b(?:senior|sr|snr|staff|principal|lead|head|director|manager|architect|"
    r"vp|vice\s+president|chief|cto|cio)\b"
)
ENTRY = re.compile(
    r"\b(?:intern|internship|trainee|apprentice|fresher|graduate|campus|"
    r"entry\s+level|junior|jr|associate)\b"
)

# Pure non-infrastructure roles.  A title that also says DevOps/SRE is allowed:
# "DevOps Engineer - Java" is a DevOps role supporting a Java stack, whereas
# "Java Developer" is not.
HARD_NEGATIVE = re.compile(
    r"""
    \b(?:data\s+analyst|data\s+scientist|data\s+engineer|machine\s+learning|ml\s+engineer|
        ai\s+engineer|artificial\s+intelligence|nlp\s+engineer|computer\s+vision|
        business\s+intelligence|analytics\s+engineer|bi\s+analyst)\b
    |\b(?:frontend|front\s+end|react(?:\.?js)?|angular|vue(?:\.?js)?|ui|ux|
        web\s+developer|full\s+stack|fullstack|wordpress)\b
    |\b(?:ios|android|mobile\s+developer|react\s+native|flutter|swift|kotlin)\b
    |\b(?:qa|quality\s+assurance|sdet|test(?:ing)?|test\s+engineer|test\s+automation|
        automation\s+tester|manual\s+tester|quality\s+engineer|tester)\b
    |\b(?:sales|salesforce|crm|erp|sap|servicenow|workday|dynamics)\b
    |\b(?:marketing|content\s+writer|copywriter|seo|social\s+media)\b
    |\b(?:recruiter|recruiting|talent\s+acquisition|human\s+resources|hr|
        people\s+operations)\b
    |\b(?:finance|accountant|accounting|payroll|audit)\b
    |\b(?:bpo|call\s+center|customer\s+support|customer\s+success|
        technical\s+support|support\s+engineer|helpdesk|service\s+desk)\b
    |\b(?:product\s+manager|project\s+manager|program\s+manager)\b
    |\b(?:backend|back\s+end|java|php|node(?:\.?js)?|dotnet|golang|ruby|c\#)\b
    |\b(?:business\s+intelligence|bi)\b
    """,
    re.VERBOSE,
)

# Domains that are allowed when the title is explicitly a platform/infra role
# ("Data Platform Engineer", "ML Infrastructure Engineer"), but still get a
# small relevance penalty.
QUALIFIER = re.compile(
    r"\b(?:data|analytics|ml|ai|machine\s+learning|mobile|security|fintech|"
    r"healthcare|ecommerce)\b"
)

_EXPLICIT_DEVOPS = re.compile(r"\b(?:devops|devsecops|sre|site\s+reliability)\b")

_CORE_PATTERNS = [
    ("DevOps", re.compile(r"\b(?:devops|devsecops|cloud\s+devops|aws\s+devops)\b")),
    ("SRE", re.compile(r"\b(?:sre|site\s+reliability|reliability\s+engineer)\b")),
    ("Platform", re.compile(r"\bplatform\s+(?:engineer|engineering|reliability|infrastructure)\b")),
    ("Cloud Infrastructure", re.compile(r"\bcloud\s+infrastructure\b")),
    ("Cloud", re.compile(r"\bcloud\s+(?:engineer|software|systems|security|operations)\b")),
    ("Infrastructure", re.compile(
        r"\binfrastructure\s+(?:(?:software|systems|cloud)\s+)?engineer\b"
        r"|\binfrastructure\s+engineering\b"
    )),
    ("Release/Build", re.compile(r"\b(?:release|build|deployment)\s+engineer\b|\bbuild\s+and\s+release\b")),
    ("Systems", re.compile(r"\b(?:systems?\s+administrator|sysadmin)\b")),
    ("Production", re.compile(r"\bproduction\s+engineer\b")),
    ("Kubernetes", re.compile(r"\bkubernetes\s+engineer\b")),
    ("AWS", re.compile(r"\baws\s+engineer\b")),
]


@dataclass(frozen=True)
class TitleInfo:
    normalized: str
    role_family: str | None
    seniority: str | None
    rejection: str | None
    explicit_devops: bool
    qualifier: str | None = None


def normalize_title(title: str) -> str:
    text = (title or "").lower().replace("&", " and ")
    text = _PUNCT.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def classify_title(title: str) -> TitleInfo:
    normalized = normalize_title(title)
    explicit_devops = bool(_EXPLICIT_DEVOPS.search(normalized))
    seniority_match = SENIORITY.search(normalized)
    entry_match = ENTRY.search(normalized)
    hard_match = HARD_NEGATIVE.search(normalized)

    role_family = None
    for name, pattern in _CORE_PATTERNS:
        if pattern.search(normalized):
            role_family = name
            break

    qualifier = QUALIFIER.search(normalized)

    rejection = None
    if seniority_match:
        rejection = f"senior/leadership title ({seniority_match.group(0)})"
    elif entry_match:
        rejection = f"entry-level title ({entry_match.group(0)})"
    elif hard_match and not explicit_devops:
        rejection = f"unrelated role family ({hard_match.group(0)})"
    elif not role_family:
        rejection = "title is not a DevOps/Cloud/SRE/Platform role"

    return TitleInfo(
        normalized=normalized,
        role_family=role_family,
        seniority=seniority_match.group(0) if seniority_match else None,
        rejection=rejection,
        explicit_devops=explicit_devops,
        qualifier=qualifier.group(0) if qualifier else None,
    )
