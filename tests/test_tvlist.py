import datetime as dt
import re
import tempfile
import unittest
from pathlib import Path

from tvlist.matcher import ShowMatcher, normalize
from tvlist import config
from tvlist.pogdesign import (
    Episode, PogClient, PogError, Show, page_is_logged_in, parse_catalogue,
    parse_show_select, parse_summary,
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

    def test_summary_tolerates_layout_changes(self):
        # Strip the schema.org attributes and rename the checkbox class: the
        # parser must still find every episode via the episode links.
        page = re.sub(r' itemprop="[^"]*"', "", fixture("summary_scrubs.html"))
        page = page.replace('class="watchcheck"', 'class="chk on"')
        eps = {e.code: e for e in parse_summary(page, today=dt.date(2026, 10, 4))}
        self.assertEqual(len(eps), 12)
        self.assertEqual(eps["S02E01"].watch_value, "41107-2-01/10-2026")
        self.assertTrue(eps["S02E01"].aired)
        self.assertFalse(eps["S02E03"].aired)

    def test_summary_builds_missing_checkbox_value(self):
        # Aired episode without a checkbox: value is built from the show id.
        page = re.sub(r'<input id="s2e02i41107"[^>]*>', "", fixture("summary_scrubs.html"))
        eps = {e.code: e for e in parse_summary(page, today=dt.date(2026, 10, 4))}
        self.assertEqual(eps["S02E02"].watch_value, "41107-2-02/10-2026")

    def test_summary_checked_input_means_watched(self):
        page = fixture("summary_scrubs.html").replace(
            'value="41107-2-01/10-2026"', 'value="41107-2-01/10-2026" checked', 1)
        eps = {e.code: e for e in parse_summary(page)}
        self.assertTrue(eps["S02E01"].watched)
        self.assertFalse(eps["S02E02"].watched)

    def test_catalogue_loose_fallback(self):
        page = """<html><body>
          <section><h3>Currently Airing</h3>
            <div class="card"><a href="https://www.pogdesign.co.uk/cat/Silo-summary"><b>Silo</b></a>
              <input type="checkbox" value="2900"></div></section>
          <h2>[ S ] - Shows Cancelled or Have Ended</h2>
            <div class="card"><a href="/cat/Sherlock-summary"><h2>Sherlock</h2></a></div>
        </body></html>"""
        shows = {s.name: s for s in parse_catalogue(page)}
        self.assertEqual(set(shows), {"Silo", "Sherlock"})
        self.assertFalse(shows["Silo"].ended)
        self.assertTrue(shows["Sherlock"].ended)
        self.assertTrue(shows["Sherlock"].url.endswith("/cat/Sherlock-summary"))

    def test_unreadable_show_page_is_reported_and_saved(self):
        class Offline(PogClient):
            def _request(self, url, data=None, ajax=False, referer=None):
                return "<html><body>Something completely different</body></html>"

        with tempfile.TemporaryDirectory() as d:
            old, config.DIR = config.DIR, Path(d)
            try:
                with self.assertRaises(PogError) as cm:
                    Offline().fetch_episodes(Show("1", "Silo", "https://x/cat/Silo-summary"))
                self.assertIn("could not read the episode list", str(cm.exception))
                self.assertTrue((Path(d) / "unreadable" / "Silo_summary.html").exists())
            finally:
                config.DIR = old

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
            files.append("Silo - Season 2/Silo - S02E03 - Title.mkv")
            files.append("9-1-1 - Nashville - Season 1/S01E01.mkv")
            for f in files:
                (root / f).parent.mkdir(parents=True, exist_ok=True)
                (root / f).write_bytes(b"")
            eps = sorted(scan([d]), key=lambda e: e.path)
            self.assertEqual(len(eps), 5)
            silo = [e for e in eps if "Silo" in e.path][0]
            self.assertEqual(silo.show_candidates[0], "Silo")
            nash = [e for e in eps if "Nashville" in e.path][0]
            self.assertEqual(nash.show_candidates[0], "9-1-1 - Nashville")
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
        self.assertEqual(normalize("Chicago P.D."), normalize("Chicago PD"))
        self.assertEqual(normalize("9-1-1: Nashville"), normalize("9-1-1 - Nashville"))

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
