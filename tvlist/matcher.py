"""Match local show folder / file names to shows in the PoGDesign catalogue."""

from __future__ import annotations

import difflib
import re
import unicodedata

from .pogdesign import Show

_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_COUNTRY_RE = re.compile(r"\b(us|uk|au|nz|ca)\b")


def normalize(name: str, keep_year: bool = True) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ").replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    if not keep_year:
        s = _YEAR_RE.sub(" ", s)
    s = re.sub(r"^the\s+", "", s.strip())
    s = re.sub(r"\s+the$", "", s)  # "Office, The"
    # Join spelled-out initials: "p d" -> "pd", "s w a t" -> "swat"
    s = re.sub(r"\b([a-z0-9])\s+(?=[a-z0-9]\b)", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def _strip_country(key: str) -> str:
    return re.sub(r"\s+", " ", _COUNTRY_RE.sub(" ", key)).strip()


class ShowMatcher:
    def __init__(self, shows: dict[str, Show], aliases: dict[str, str] | None = None):
        self.shows = shows
        self.exact: dict[str, list[Show]] = {}
        self.loose: dict[str, list[Show]] = {}
        for s in shows.values():
            slug = s.url.rsplit("/", 1)[-1].removesuffix("-summary").replace("-", " ")
            for key in {normalize(s.name), normalize(slug)}:
                self.exact.setdefault(key, []).append(s)
            for key in {normalize(s.name, False), normalize(slug, False)}:
                self.loose.setdefault(key, []).append(s)
                bare = _strip_country(key)
                if bare != key:
                    self.loose.setdefault(bare, []).append(s)
        # aliases: local name -> site show name, slug or id
        self.aliases = {normalize(k): v for k, v in (aliases or {}).items()}
        self._cache: dict[str, Show | None] = {}

    @staticmethod
    def _pick(cands: list[Show], local: str) -> Show:
        cands = list({c.id: c for c in cands}.values())
        if len(cands) == 1:
            return cands[0]
        tracked = [c for c in cands if c.tracked]
        if len(tracked) == 1:
            return tracked[0]
        pool = tracked or cands
        # Local name without a year: prefer the site entry without a year too,
        # otherwise the most recent (highest id) revival.
        if not _YEAR_RE.search(local):
            no_year = [c for c in pool if not _YEAR_RE.search(c.name)]
            if len(no_year) == 1:
                return no_year[0]
        return max(pool, key=lambda c: int(c.id) if c.id.isdigit() else 0)

    def _alias(self, target: str) -> Show | None:
        if target in self.shows:
            return self.shows[target]
        hits = self.exact.get(normalize(target)) or self.exact.get(
            normalize(target.removesuffix("-summary").replace("-", " "))
        )
        return self._pick(hits, target) if hits else None

    def match(self, local_name: str) -> Show | None:
        key = normalize(local_name)
        if key in self._cache:
            return self._cache[key]
        result: Show | None = None
        if key in self.aliases:
            result = self._alias(self.aliases[key])
        if result is None and key in self.exact:
            result = self._pick(self.exact[key], local_name)
        if result is None:
            loose = normalize(local_name, False)
            if loose in self.loose:
                result = self._pick(self.loose[loose], local_name)
            elif loose:
                # Drop country tags ("The Office US") as a last exact attempt.
                no_country = _strip_country(loose)
                if no_country in self.loose:
                    result = self._pick(self.loose[no_country], local_name)
        if result is None and key:
            close = difflib.get_close_matches(key, list(self.exact), n=1, cutoff=0.9)
            if close:
                result = self._pick(self.exact[close[0]], local_name)
        self._cache[key] = result
        return result

    def match_any(self, candidates: tuple[str, ...]) -> Show | None:
        for c in candidates:
            s = self.match(c)
            if s:
                return s
        return None
