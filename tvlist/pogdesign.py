"""Minimal client for the PoGDesign TV Calendar (https://www.pogdesign.co.uk/cat/).

Uses only the Python standard library. The site has no public API, so this
module talks to the same endpoints the website's own JavaScript uses:

* ``POST /cat/login``        - form login (fields ``username`` / ``password``)
* ``GET  /cat/all-shows/<L>`` - catalogue of every show (airing and ended)
* ``GET  /cat/show-select``   - show picker; your filtered shows are ``checked``
* ``GET  /cat/<Slug>-summary`` - full episode guide for one show
* ``POST /cat/watchhandle``   - mark an episode watched (``watched=adding``)
"""

from __future__ import annotations

import datetime as _dt
import html
import http.cookiejar
import re
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

BASE = "https://www.pogdesign.co.uk/cat/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class PogError(Exception):
    pass


class LoginError(PogError):
    pass


@dataclass
class Show:
    id: str
    name: str
    url: str  # .../<Slug>-summary
    ended: bool = False
    tracked: bool = False  # in the user's favourites filter


@dataclass
class Episode:
    season: int
    number: int
    title: str
    air_date: _dt.date | None
    aired: bool  # site says AIRED (and has a watch checkbox)
    watched: bool
    watch_value: str | None  # value POSTed to /cat/watchhandle

    @property
    def code(self) -> str:
        return f"S{self.season:02d}E{self.number:02d}"

    def released(self, today: _dt.date | None = None) -> bool:
        today = today or _dt.date.today()
        if not self.aired:
            return False
        return self.air_date is None or self.air_date <= today


# --------------------------------------------------------------------------
# HTML parsing (kept separate from the network so it can be unit tested)
# --------------------------------------------------------------------------

_CATALOGUE_SECTION_RE = re.compile(r"<h2[^>]*>\s*\[[^\]]*\]\s*-\s*([^<]+)</h2>", re.I)
_CATALOGUE_BOX_RE = re.compile(
    r'<div class="contbox prembox([^"]*)">(.*?)</div>', re.S
)
_SUMMARY_LINK_RE = re.compile(r'<a href="([^"]+-summary)"')
_H2_RE = re.compile(r"<h2>(.*?)</h2>", re.S)
_CHECKBOX_VALUE_RE = re.compile(r'<input[^>]*type="checkbox"[^>]*value="([^"]+)"')


def parse_catalogue(page: str) -> list[Show]:
    """Parse one ``/cat/all-shows/<letter>`` page."""
    # Split the page into (section heading, chunk) pieces so we know which
    # shows are under "Shows Cancelled or Have Ended".
    shows: list[Show] = []
    sections = _CATALOGUE_SECTION_RE.split(page)
    # sections = [pre, heading1, chunk1, heading2, chunk2, ...]
    for i in range(1, len(sections) - 1, 2):
        heading, chunk = sections[i].lower(), sections[i + 1]
        ended = "ended" in heading or "cancel" in heading
        for m in _CATALOGUE_BOX_RE.finditer(chunk):
            classes, body = m.group(1).split(), m.group(2)
            link = _SUMMARY_LINK_RE.search(body)
            name = _H2_RE.search(body)
            sid = _CHECKBOX_VALUE_RE.search(body)
            if not (link and name and sid):
                continue
            shows.append(
                Show(
                    id=sid.group(1),
                    name=html.unescape(re.sub(r"<[^>]+>", "", name.group(1))).strip(),
                    url=urllib.parse.urljoin(BASE, link.group(1)),
                    ended=ended,
                    # Boxes for shows that are *not* in your filter carry the
                    # "removed" class; anything else is treated as tracked.
                    tracked=bool(classes) and "removed" not in classes,
                )
            )
    return shows


_SELECT_RE = re.compile(
    r'<li id="check(\d+)" class="selectgrp ([^"]*)">.*?alt="([^"]*)".*?href="([^"]+-summary)"',
    re.S,
)


def parse_show_select(page: str) -> list[Show]:
    """Parse ``/cat/show-select``; ``tracked`` reflects the user's filter."""
    out = []
    for sid, classes, name, url in _SELECT_RE.findall(page):
        out.append(
            Show(
                id=sid,
                name=html.unescape(name).strip(),
                url=urllib.parse.urljoin(BASE, url),
                tracked="checked" in classes.split(),
            )
        )
    return out


_EP_LI_RE = re.compile(r'<li class="ep ([^"]*)"[^>]*itemprop="episode"[^>]*>(.*?)</li>', re.S)
_SEASON_RE = re.compile(r'itemprop="seasonNumber">\s*(\d+)\s*<')
_EPNUM_RE = re.compile(r'itemprop="episodeNumber"\s+content="(\d+)"')
_DATE_RE = re.compile(r'itemprop="releasedEvent"\s+content="(\d{4}-\d{2}-\d{2})"')
_TITLE_RE = re.compile(r'itemprop="name"[^>]*>\s*<a[^>]*>(.*?)</a>', re.S)
_WATCH_INPUT_RE = re.compile(r'<input[^>]*class="watchcheck"[^>]*>')
_VALUE_RE = re.compile(r'value="([^"]+)"')


def parse_summary(page: str) -> list[Episode]:
    """Parse a show's ``-summary`` page into its episode list."""
    eps: list[Episode] = []
    for m in _EP_LI_RE.finditer(page):
        classes, body = m.group(1).split(), m.group(2)
        season = _SEASON_RE.search(body)
        num = _EPNUM_RE.search(body)
        if not (season and num):
            continue
        date_m = _DATE_RE.search(body)
        try:
            air_date = _dt.date.fromisoformat(date_m.group(1)) if date_m else None
        except ValueError:
            air_date = None
        title_m = _TITLE_RE.search(body)
        inp = _WATCH_INPUT_RE.search(body)
        value = _VALUE_RE.search(inp.group(0)).group(1) if inp and _VALUE_RE.search(inp.group(0)) else None
        aired = "punaired" not in body and value is not None
        watched = "infochecked" in classes or bool(inp and re.search(r"\bchecked\b", inp.group(0)))
        eps.append(
            Episode(
                season=int(season.group(1)),
                number=int(num.group(1)),
                title=html.unescape(re.sub(r"<[^>]+>", "", title_m.group(1))).strip() if title_m else "",
                air_date=air_date,
                aired=aired,
                watched=watched,
                watch_value=value,
            )
        )
    return eps


def page_is_logged_in(page: str) -> bool:
    """Heuristic: a logged-in page has no login form and offers a logout."""
    if 'id="login_form"' in page:
        return False
    return bool(re.search(r"log\s*-?out", page, re.I))


# --------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------


@dataclass
class PogClient:
    delay: float = 0.3  # seconds between requests; be polite to a small site
    timeout: float = 30.0
    _last: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )
        self.opener.addheaders = [
            ("User-Agent", USER_AGENT),
            ("Accept-Language", "en-GB,en;q=0.9"),
        ]

    # -- low level ---------------------------------------------------------
    def _request(self, url: str, data: dict | None = None, ajax: bool = False) -> str:
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        url = urllib.parse.urljoin(BASE, url)
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body)
        req.add_header("Referer", BASE)
        if ajax:
            req.add_header("X-Requested-With", "XMLHttpRequest")
        for attempt in range(3):
            try:
                with self.opener.open(req, timeout=self.timeout) as resp:
                    self._last = time.monotonic()
                    charset = resp.headers.get_content_charset() or "utf-8"
                    return resp.read().decode(charset, errors="replace")
            except urllib.error.HTTPError as e:
                if e.code < 500 or attempt == 2:
                    raise PogError(f"HTTP {e.code} for {url}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt == 2:
                    raise PogError(f"Network error for {url}: {e}") from e
            time.sleep(2 ** (attempt + 1))
        raise PogError(f"Request failed: {url}")  # pragma: no cover

    # -- high level --------------------------------------------------------
    def login(self, email: str, password: str) -> None:
        self._request("login")  # pick up session cookie
        page = self._request(
            "login", {"username": email, "password": password, "sub_login": ""}
        )
        if not page_is_logged_in(page):
            # Some redirects land on a page without a logout link; double-check.
            page = self._request("")
            if not page_is_logged_in(page):
                raise LoginError("Login failed - check your e-mail and password.")

    def fetch_catalogue(self, progress=None) -> dict[str, Show]:
        shows: dict[str, Show] = {}
        letters = ["0"] + list(string.ascii_uppercase)
        for i, letter in enumerate(letters):
            if progress:
                progress(f"Downloading show catalogue {i + 1}/{len(letters)} ({letter})")
            for s in parse_catalogue(self._request(f"all-shows/{letter}")):
                shows.setdefault(s.id, s)
        # The show-select page reliably marks which shows are in your filter.
        try:
            for s in parse_show_select(self._request("show-select")):
                if s.id in shows:
                    shows[s.id].tracked = shows[s.id].tracked or s.tracked
                else:
                    shows[s.id] = s
        except PogError as e:  # non fatal
            if progress:
                progress(f"Warning: could not read show-select page ({e})")
        return shows

    def fetch_episodes(self, show: Show) -> list[Episode]:
        return parse_summary(self._request(show.url))

    def mark_watched(self, ep: Episode) -> None:
        if not ep.watch_value:
            raise PogError(f"{ep.code} has no watch checkbox (not aired yet?)")
        self._request(
            "watchhandle", {"watched": "adding", "shid": ep.watch_value}, ajax=True
        )
