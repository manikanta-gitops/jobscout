"""Candidate profile derived from Manikanta's resume.

Single source of truth for matching. Skills are grouped as core (must-have)
and supporting. Salary, work arrangement and relocation are intentionally not
encoded as hard filters.
"""

from __future__ import annotations

import re

YEARS_EXPERIENCE = 3.5  # June 2023 - present
MAX_YEARS_EXPERIENCE = 4  # only roles whose requirement is <= 4 years
TARGET_MIN_YEARS = 1
HOME = "Hyderabad, India"

# Core stack. Satisfied by ANY alias. Regexes use word boundaries so "laws"
# never counts as AWS and "shipping containers" never counts as Docker.
MUST_HAVE = [
    ("AWS", r"\baws\b|\bamazon web services\b"),
    ("Kubernetes/EKS", r"\bkubernetes\b|\bk8s\b|\bamazon eks\b|\beks\b|\bopenshift\b"),
    ("Terraform", r"\bterraform\b|\binfrastructure as code\b|\biac\b"),
    ("Docker", r"\bdocker\b|\bcontaineri[sz](?:ed|ation)\b"),
    ("Helm", r"\bhelm\b|\bhelm charts?\b"),
    ("CI/CD", r"\bci\s*/\s*cd\b|continuous (?:integration|deployment|delivery)|"
              r"github actions|gitlab ci|jenkins|circleci|buildkite|azure devops|"
              r"bitbucket pipelines"),
    ("GitOps/ArgoCD", r"\bgitops\b|\bargocd\b|\bargo cd\b"),
    ("Observability", r"\bobservability\b|\bdatadog\b|\bprometheus\b|\bgrafana\b|"
                      r"\bcloudwatch\b|\bapm\b|distributed trac|\btracing\b"),
]

# Weighted contribution of each core skill.  AWS/Kubernetes/Terraform/CI-CD
# are the anchors; the others add evidence without dominating.
CORE_WEIGHTS = {
    "AWS": 10,
    "Kubernetes/EKS": 10,
    "Terraform": 9,
    "Docker": 6,
    "Helm": 4,
    "CI/CD": 9,
    "GitOps/ArgoCD": 6,
    "Observability": 6,
}

# Useful but not deal-breaking.
SUPPORTING = [
    ("AWS IAM", r"\biam\b"),
    ("GitHub OIDC", r"\boidc\b|openid connect"),
    ("Snyk", r"\bsnyk\b"),
    ("Python", r"\bpython\b"),
    ("Java/Spring Boot", r"spring boot|\bjava\b"),
    ("Bash/Shell", r"\bbash\b|\bshell script"),
    ("Gradle", r"\bgradle\b"),
    ("Maven", r"\bmaven\b"),
    ("Linux", r"\blinux\b"),
    ("AWS Networking", r"\bvpc\b|\broute ?53\b|\balb\b|load balancer"),
    ("AWS Secrets", r"secrets manager"),
    ("Entra ID/RBAC", r"entra id|azure ad|\brbac\b"),
    ("Slack alerting", r"\bslack\b"),
    ("Rollbar", r"\brollbar\b"),
]

# Kept for compatibility with older imports.  Title decisions live in
# taxonomy.py now.
FAMILY_TITLE = (
    r"devops|devsecops|\bsre\b|site reliability|reliability engineer|platform engineer"
    r"|cloud engineer|infrastructure engineer|production engineer|cloud devops"
    r"|aws devops|release engineer|build engineer|build and release|cloud operations"
    r"|systems administrator|devops support|cloud infrastructure|platform engineering"
)
TECH_TITLE = (
    r"\baws\b|kubernetes|\bk8s\b|\beks\b|terraform|\bhelm\b|gitops|argocd|"
    r"github actions|\bdocker\b|\bcloud\b"
)
SENIOR_TITLE = (
    r"\bsenior\b|\bsr\.?\b|\bsnr\b|\bstaff\b|\bprincipal\b|\blead\b|\bhead\b|"
    r"manager|director|architect|\bexpert\b|\bspecialist\b"
)
ENTRY_LEVEL = r"\bintern\b|\binternship\b|\btrainee\b|\bgraduate\b|\bfresher\b|\bcampus\b"
UNRELATED_TITLE = (
    r"\bsales\b|marketing|recruit|talent acquisition|human resources|\bhr\b|finance"
    r"|accountant|business development|account manager|graphic|\bux\b|ui designer"
    r"|technical writer|content writer|data scientist|data analyst|machine learning"
    r"|data engineer|python developer|java developer|\bphp\b|full stack|frontend"
    r"|front[- ]end|back[- ]end|web developer|ios|android|game developer"
    r"|\bqa\b|quality assurance|test engineer|sdet|automation testing"
    r"|product manager|project manager|program manager|customer success"
    r"|technical support|helpdesk|service desk|virtual assistant"
)

INDIA_TOKEN = (
    r"\bindia\b|hyderabad|bangalore|bengaluru|chennai|pune|mumbai|delhi|noida"
    r"|gurgaon|gurugram|kolkata|ahmedabad|indore|coimbatore|kochi"
)
UNVERIFIED_TOKEN = r"\bremote\b|remotely|anywhere|worldwide|global|\bapac\b|\basia\b|distributed"


def _compile_group(groups: list[tuple[str, str]]) -> list[tuple[str, re.Pattern[str]]]:
    return [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in groups]
