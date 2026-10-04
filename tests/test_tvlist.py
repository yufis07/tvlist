import datetime as dt
import tempfile
import unittest
from pathlib import Path

from tvlist.matcher import ShowMatcher, normalize
from tvlist.pogdesign import (
    Episode, Show, page_is_logged_in, parse_catalogue, parse_show_select, parse_summary,
)
from tvlist.scanner import parse_episode_numbers, scan
from tvlist.sync import Options, run_sync

FIX = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class ParseSite(unittest.TestCase):
    def test_summary(self):
        eps = parse_summary(fixture("summary_scrubs.html"))
        self.assertEqual(len(eps), 12)
        s2 = [e for e in eps if e.season == 2]
        self.assertEqual([e.number for e in s2], [1, 2, 3])
        self.assertEqual(s2[0].title, "My First Time in a While")
        self.assertEqual(s2[0].air_date, dt.date(2026, 10, 1))
        self.assertEqual(s2[0].watch_value, "41107-2-01/10-2026")
        self.assertTrue(s2[0].aired)
        self.assertFalse(s2[2].aired)  # UNAIRED, no checkbox
        self.assertIsNone(s2[2].watch_value)
        self.assertFalse(s2[2].released(dt.date(2026, 10, 4)))
        self.assertFalse(any(e.watched for e in eps))

    def test_summary_watched_state(self):
        page = fixture("summary_scrubs.html").replace('class="ep info ', 'class="ep infochecked ', 1)
        eps = parse_summary(page)
        self.assertEqual(sum(e.watched for e in eps), 1)

    def test_catalogue(self):
        shows = parse_catalogue(fixture("allshows_E.html"))
        names = {s.name: s for s in shows}
        self.assertIn("East of Eden", names)
        self.assertFalse(names["East of Eden"].ended)
        self.assertTrue(names["Earth Abides"].ended)
        self.assertEqual(names["East of Eden"].id, "41317")
        self.assertTrue(names["East of Eden"].url.endswith("/cat/East-of-Eden-summary"))
        self.assertFalse(any(s.tracked for s in shows))

    def test_show_select(self):
        shows = {s.name: s for s in parse_show_select(fixture("show_select.html"))}
        self.assertIn("Scrubs (2026)", shows)
        self.assertEqual(shows["Scrubs (2026)"].id, "41107")
        self.assertEqual(sum(s.tracked for s in shows.values()), 1)

    def test_login_detection(self):
        self.assertFalse(page_is_logged_in(fixture("login_form.html")))
        self.assertTrue(page_is_logged_in('<a href="/cat/logout">Log Out</a>'))


class ParseFiles(unittest.TestCase):
    def test_patterns(self):
        cases = {
            "Show.Name.S01E02.720p.mkv": (1, (2,)),
            "show name - s1e2 - title": (1, (2,)),
            "Show S01E01E02": (1, (1, 2)),
            "Show S01E01-E03 1080p": (1, (1, 2, 3)),
            "Show.S02E05-06": (2, (5, 6)),
            "Show 3x07 Title": (3, (7,)),
            "Show.S10E100.x264": (10, (100,)),
        }
        for name, want in cases.items():
            got = parse_episode_numbers(name)
            self.assertIsNotNone(got, name)
            self.assertEqual(got[:2], want, name)
        self.assertIsNone(parse_episode_numbers("Holiday Video 2019"))

    def test_scan_layouts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            files = [
                "Scrubs (2026)/Season 01/Scrubs (2026) - S01E01.mkv",
                "Scrubs (2026)/Season 02/S02E02.mp4",
                "Loose.Show.Name.S03E04.HDTV.avi",
                "Scrubs (2026)/Season 01/sample-S01E01.mkv",
                "Scrubs (2026)/notes.txt",
            ]
            for f in files:
                (root / f).parent.mkdir(parents=True, exist_ok=True)
                (root / f).write_bytes(b"")
            eps = sorted(scan([d]), key=lambda e: e.path)
            self.assertEqual(len(eps), 3)
            loose = [e for e in eps if "Loose" in e.path][0]
            self.assertEqual(loose.show_candidates[0], "Loose Show Name")
            scrubs = [e for e in eps if "Scrubs" in e.path]
            self.assertTrue(all(e.show_candidates[0] == "Scrubs (2026)" for e in scrubs))


class Matching(unittest.TestCase):
    def setUp(self):
        mk = lambda i, n, slug, tracked=False: Show(i, n, f"https://x/cat/{slug}-summary", tracked=tracked)
        self.shows = {s.id: s for s in [
            mk("1", "Scrubs", "Scrubs"),
            mk("41107", "Scrubs (2026)", "Scrubs-2026"),
            mk("3", "The Office (US)", "The-Office-US", tracked=True),
            mk("4", "Grey's Anatomy", "Greys-Anatomy"),
            mk("5", "Law & Order: SVU", "Law-and-Order-SVU"),
        ]}
        self.m = ShowMatcher(self.shows, {"SVU": "Law & Order: SVU"})

    def test_normalize(self):
        self.assertEqual(normalize("The Office (US)"), "office us")
        self.assertEqual(normalize("Office, The"), "office")

    def test_match(self):
        self.assertEqual(self.m.match("Scrubs").id, "1")
        self.assertEqual(self.m.match("Scrubs (2026)").id, "41107")
        self.assertEqual(self.m.match("Scrubs 2026").id, "41107")
        self.assertEqual(self.m.match("Greys Anatomy").id, "4")
        self.assertEqual(self.m.match("the office us").id, "3")
        self.assertEqual(self.m.match("The Office").id, "3")
        self.assertEqual(self.m.match("SVU").id, "5")
        self.assertIsNone(self.m.match("Totally Unknown Show"))


class FakeClient:
    def __init__(self, ticks_stick=True):
        self.marked = []
        self.logged_in = None
        self.ticks_stick = ticks_stick

    def login(self, email, password):
        self.logged_in = email

    def fetch_catalogue(self, progress=None):
        return {
            "10": Show("10", "Alpha", "https://x/cat/Alpha-summary"),
            "20": Show("20", "Beta", "https://x/cat/Beta-summary", tracked=True),
        }

    def fetch_episodes(self, show):
        eps = self._episodes(show)
        if self.ticks_stick:
            for e in eps:
                e.watched = e.watched or e.watch_value in self.marked
        return eps

    def _episodes(self, show):
        d = dt.date
        if show.id == "10":
            return [
                Episode(1, 1, "a1", d(2026, 1, 1), True, False, "10-1-1/1-2026"),
                Episode(1, 2, "a2", d(2026, 1, 8), True, True, "10-1-2/1-2026"),
                Episode(1, 3, "a3", d(2026, 1, 15), True, False, "10-1-3/1-2026"),
                Episode(1, 4, "a4", d(2026, 12, 1), False, False, None),  # future
            ]
        return [
            Episode(1, 1, "b1", d(2025, 5, 1), True, False, "20-1-1/5-2025"),
            Episode(1, 2, "b2", d(2030, 1, 1), False, False, None),
        ]

    def mark_watched(self, ep, show=None):
        self.marked.append(ep.watch_value)


class EndToEnd(unittest.TestCase):
    def run_with(self, client=None, **kw):
        with tempfile.TemporaryDirectory() as d:
            for f in ["Alpha/Season 1/Alpha - S01E01.mkv", "Alpha/Season 1/Alpha - S01E02.mkv",
                      "Alpha/Season 1/Alpha - S01E04.mkv", "Gamma/Gamma.S01E01.mkv"]:
                p = Path(d) / f
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"")
            client = client or FakeClient()
            opts = Options(folders=[d], email="me@example.com", password="pw", **kw)
            rep = run_sync(opts, log=lambda m: None, client=client, today=dt.date(2026, 10, 4))
            return rep, client

    def test_sync(self):
        rep, client = self.run_with()
        self.assertEqual(client.marked, ["10-1-1/1-2026"])  # E02 already watched
        res = {r.show.name: r for r in rep.results}
        self.assertEqual([e.code for e in res["Alpha"].missing], ["S01E03"])  # E04 not aired
        self.assertEqual(res["Alpha"].already_watched, 1)
        self.assertEqual(res["Alpha"].extra_local, ["S01E04"])
        self.assertEqual([e.code for e in res["Beta"].missing], ["S01E01"])
        self.assertEqual(rep.unmatched_local, {"Gamma": 1})
        text = rep.to_text()
        self.assertIn("MISSING RELEASED EPISODES", text)
        self.assertIn("Beta [no files on disk]", text)
        self.assertIn("Alpha <- local 'Alpha'", text)
        self.assertEqual(res["Alpha"].unconfirmed, [])
        self.assertNotIn("TICKS NOT CONFIRMED", text)

    def test_ticks_that_do_not_stick_are_reported(self):
        rep, client = self.run_with(client=FakeClient(ticks_stick=False))
        res = {r.show.name: r for r in rep.results}
        self.assertEqual([e.code for e in res["Alpha"].unconfirmed], ["S01E01"])
        text = rep.to_text()
        self.assertIn("TICKS NOT CONFIRMED", text)
        self.assertIn("Alpha: S1: E01", text)

    def test_dry_run_and_only_local(self):
        rep, client = self.run_with(dry_run=True, include_tracked=False)
        self.assertEqual(client.marked, [])
        self.assertEqual([r.show.name for r in rep.results], ["Alpha"])
        self.assertEqual(len(rep.results[0].marked), 1)


if __name__ == "__main__":
    unittest.main()
