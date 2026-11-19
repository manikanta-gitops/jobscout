"""Split a description into required / preferred sections.

Sources keep paragraph breaks now, so headings such as "Requirements:" and
"Nice to have:" can be honoured.  The fallback split avoids the old bare
"preferred"/"bonus" words that used to cut the requirements section at
"Preferred location:" or salary text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REQUIRED_HEADING = re.compile(
    r"^\s*(?:requirements?|required(?:\s+skills|\s+qualifications|\s+experience)?|"
    r"must[-\s]have(?:\s+skills)?|qualifications|basic\s+qualifications|"
    r"minimum\s+qualifications|what\s+you(?:'|’)ll\s+need|"
    r"what\s+we(?:'|’)re\s+looking\s+for|your\s+background|"
    r"responsibilities|what\s+you(?:'|’)ll\s+do)\s*:?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)

PREFERRED_HEADING = re.compile(
    r"^\s*(?:nice[-\s]to[-\s]have(?:\s+skills)?|"
    r"preferred(?:\s+skills|\s+qualifications|\s+experience)?|"
    r"bonus(?:\s+points)?|desirable|good[-\s]to[-\s]have|plus\s+points)\s*:?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)

OTHER_HEADING = re.compile(
    r"^\s*(?:about\s+us|benefits|perks|compensation|"
    r"why\s+join|what\s+we\s+offer)\s*:?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)

FALLBACK_SPLIT = re.compile(
    r"\b(?:nice[-\s]to[-\s]have(?:\s+skills)?|"
    r"preferred\s+(?:skills|qualifications|experience)|"
    r"bonus\s+points|desirable|good[-\s]to[-\s]have)\b",
    re.IGNORECASE,
)

_LOCATION_WORDS = {"location", "locations", "work", "remote", "city", "cities", "mode"}


@dataclass(frozen=True)
class Sections:
    required: str
    preferred: str
    all: str
    has_headings: bool


def split_sections(text: str) -> Sections:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    required: list[str] = []
    preferred: list[str] = []
    current = "required"
    has_headings = False

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            if current == "preferred":
                preferred.append("")
            elif current == "required":
                required.append("")
            continue

        preferred_match = PREFERRED_HEADING.match(stripped) if not _is_location_line(stripped) else None
        required_match = REQUIRED_HEADING.match(stripped)
        other_match = OTHER_HEADING.match(stripped)

        if preferred_match:
            has_headings = True
            current = "preferred"
            rest = preferred_match.group("rest").strip()
            if rest:
                preferred.append(rest)
            continue
        if required_match:
            has_headings = True
            current = "required"
            rest = required_match.group("rest").strip()
            if rest:
                required.append(rest)
            continue
        if other_match:
            has_headings = True
            current = "other"
            continue

        if current == "preferred":
            preferred.append(stripped)
        elif current == "required":
            required.append(stripped)

    required_text = "\n".join(required).strip()
    preferred_text = "\n".join(preferred).strip()

    if not has_headings:
        match = FALLBACK_SPLIT.search(text)
        if match:
            required_text = text[: match.start()].strip()
            preferred_text = text[match.end():].strip()
        else:
            required_text = text
            preferred_text = ""

    if not required_text and preferred_text:
        required_text = preferred_text
        preferred_text = ""

    return Sections(
        required=required_text,
        preferred=preferred_text,
        all=text,
        has_headings=has_headings,
    )


def _is_location_line(line: str) -> bool:
    lowered = line.lower()
    if not lowered.startswith("preferred"):
        return False
    rest = lowered[len("preferred"):].lstrip(" :")
    first = re.split(r"\W+", rest, maxsplit=1)[0] if rest else ""
    return first in _LOCATION_WORDS
