"""robots.txt rules, read as RFC 9309 says.

The standard library parser applies the first matching rule and ignores
wildcards. RFC 9309 (and the big search engines) apply the MOST SPECIFIC
rule: the longest matching pattern wins, and "Allow" wins a tie. Patterns
may hold "*" (any characters) and end with "$" (end of the address).

Example (jobs.totalenergies.com): "Allow: /*/careers" then "Disallow: /"
allows /fr_FR/careers/SearchJobs/ and forbids everything else.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)
    crawl_delay: float | None = None


def _pattern_regex(pattern: str) -> re.Pattern:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile(regex + ("$" if anchored else ""))


class RobotRules:
    def __init__(self, text: str = "", disallow_all: bool = False) -> None:
        self.disallow_all = disallow_all
        self._groups = self._parse(text)

    @staticmethod
    def _parse(text: str) -> list[_Group]:
        groups: list[_Group] = []
        current: _Group | None = None
        last_was_agent = False
        # Some files start with a byte order mark, which would hide the first line.
        for raw_line in text.lstrip(chr(0xFEFF)).splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if current is None or not last_was_agent:
                    current = _Group()
                    groups.append(current)
                current.agents.append(value.lower())
                last_was_agent = True
                continue
            last_was_agent = False
            if current is None:
                continue
            if key in ("allow", "disallow") and value:
                current.rules.append((key == "allow", unquote(value)))
            elif key == "crawl-delay":
                try:
                    current.crawl_delay = float(value)
                except ValueError:
                    pass
        return groups

    def _groups_for(self, agent: str) -> list[_Group]:
        token = agent.split("/", 1)[0].strip().lower()
        own = [g for g in self._groups if token in g.agents]
        return own or [g for g in self._groups if "*" in g.agents]

    def can_fetch(self, agent: str, url: str) -> bool:
        if self.disallow_all:
            return False
        parts = urlsplit(url)
        path = unquote(parts.path or "/") + (f"?{unquote(parts.query)}" if parts.query else "")
        best_length, best_allow = -1, True
        for group in self._groups_for(agent):
            for allow, pattern in group.rules:
                if _pattern_regex(pattern).match(path):
                    length = len(pattern)
                    if length > best_length or (length == best_length and allow):
                        best_length, best_allow = length, allow
        return best_allow

    def crawl_delay(self, agent: str) -> float | None:
        delays = [g.crawl_delay for g in self._groups_for(agent) if g.crawl_delay is not None]
        return max(delays) if delays else None
