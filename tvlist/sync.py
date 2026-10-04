"""Compare local episodes with PoGDesign, mark watched, and report what's missing."""

from __future__ import annotations

import datetime as _dt
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .matcher import ShowMatcher
from .pogdesign import Episode, PogClient, PogError, Show
from .scanner import LocalEpisode, scan


@dataclass
class Options:
    folders: list[str]
    email: str
    password: str
    dry_run: bool = False
    # Also report missing episodes for shows in your PoGDesign filter that
    # have no files on disk at all.
    include_tracked: bool = True
    include_specials: bool = False
    # Hide missing episodes you already marked watched on the site.
    hide_watched_missing: bool = False
    aliases: dict[str, str] = field(default_factory=dict)
    # Save every page PoGDesign returns into this folder (troubleshooting).
    debug_dir: str | None = None


@dataclass
class ShowResult:
    show: Show
    local_files: int = 0
    marked: list[Episode] = field(default_factory=list)
    already_watched: int = 0
    missing: list[Episode] = field(default_factory=list)
    extra_local: list[str] = field(default_factory=list)  # on disk but unknown to site
    # Ticked, but the show page still shows them unwatched afterwards.
    unconfirmed: list[Episode] = field(default_factory=list)
    local_names: set[str] = field(default_factory=set)
    error: str | None = None


@dataclass
class SyncReport:
    results: list[ShowResult] = field(default_factory=list)
    unmatched_local: dict[str, int] = field(default_factory=dict)  # name -> file count
    local_files: int = 0
    dry_run: bool = False
    generated: _dt.datetime = field(default_factory=_dt.datetime.now)

    def to_text(self) -> str:
        return format_report(self)


def _ranges(nums: list[int]) -> str:
    nums = sorted(set(nums))
    parts, start = [], None
    for i, n in enumerate(nums):
        if start is None:
            start = n
        if i == len(nums) - 1 or nums[i + 1] != n + 1:
            parts.append(f"E{start:02d}" if start == n else f"E{start:02d}-E{n:02d}")
            start = None
    return ", ".join(parts)


def format_report(rep: SyncReport) -> str:
    L: list[str] = []
    L.append(f"TV List sync report - {rep.generated:%Y-%m-%d %H:%M}")
    if rep.dry_run:
        L.append("DRY RUN: nothing was changed on PoGDesign.")
    L.append("")

    total_marked = sum(len(r.marked) for r in rep.results)
    total_missing = sum(len(r.missing) for r in rep.results)
    verb = "Would mark" if rep.dry_run else "Marked"
    L.append(f"{verb} {total_marked} episode(s) as watched across "
             f"{sum(1 for r in rep.results if r.marked)} show(s).")
    L.append(f"{total_missing} released episode(s) are missing from your drive.")
    L.append(f"Episode files found on disk: {rep.local_files}.")
    failed = sum(1 for r in rep.results if r.error)
    if failed:
        L.append(f"WARNING: {failed} show page(s) could not be read, so those shows were "
                 "not checked - see ERRORS below.")
    total_unconfirmed = sum(len(r.unconfirmed) for r in rep.results)
    if total_unconfirmed:
        L.append(f"WARNING: {total_unconfirmed} tick(s) did not stick on PoGDesign - "
                 "see 'TICKS NOT CONFIRMED' below.")
    L.append("")

    L.append("=" * 70)
    L.append("MISSING RELEASED EPISODES (on PoGDesign, not on your drive)")
    L.append("=" * 70)
    any_missing = False
    for r in sorted(rep.results, key=lambda r: r.show.name.lower()):
        if not r.missing:
            continue
        any_missing = True
        tag = " [no files on disk]" if r.local_files == 0 else ""
        tag += " [ended]" if r.show.ended else ""
        L.append(f"\n{r.show.name}{tag} - {len(r.missing)} missing")
        by_season: dict[int, list[Episode]] = defaultdict(list)
        for e in r.missing:
            by_season[e.season].append(e)
        for season in sorted(by_season):
            eps = by_season[season]
            if r.local_files == 0 or len(eps) > 12:
                # Compact form for big gaps.
                L.append(f"   Season {season}: {_ranges([e.number for e in eps])}")
            else:
                for e in eps:
                    date = e.air_date.isoformat() if e.air_date else "?"
                    seen = "  (marked watched on site)" if e.watched else ""
                    L.append(f"   {e.code}  {date}  {e.title}{seen}")
    if not any_missing:
        L.append("\nNothing missing" + (" among the shows that could be read." if failed
                                         else " - you have every released episode."))

    L.append("")
    L.append("=" * 70)
    L.append(f"{'WOULD MARK' if rep.dry_run else 'MARKED'} AS WATCHED")
    L.append("=" * 70)
    for r in sorted(rep.results, key=lambda r: r.show.name.lower()):
        if r.marked:
            L.append(f"\n{r.show.name}: {len(r.marked)} episode(s)")
            by_season: dict[int, list[int]] = defaultdict(list)
            for e in r.marked:
                by_season[e.season].append(e.number)
            for season in sorted(by_season):
                L.append(f"   Season {season}: {_ranges(by_season[season])}")
    if not total_marked:
        L.append("\nNothing new to mark.")

    unconf = [r for r in rep.results if r.unconfirmed]
    if unconf:
        L.append("")
        L.append("=" * 70)
        L.append("TICKS NOT CONFIRMED (sent to the site, but the show page still shows")
        L.append("them as unwatched afterwards)")
        L.append("=" * 70)
        for r in unconf:
            by_season: dict[int, list[int]] = defaultdict(list)
            for e in r.unconfirmed:
                by_season[e.season].append(e.number)
            seasons = "; ".join(f"S{s}: {_ranges(n)}" for s, n in sorted(by_season.items()))
            L.append(f"   {r.show.name}: {seasons}")

    errors = [r for r in rep.results if r.error]
    if errors:
        L.append("")
        L.append("=" * 70)
        L.append("ERRORS")
        L.append("=" * 70)
        for r in errors:
            L.append(f"   {r.show.name}: {r.error}")

    extras = [r for r in rep.results if r.extra_local]
    if extras:
        L.append("")
        L.append("=" * 70)
        L.append("LOCAL EPISODES NOT LISTED (OR NOT YET AIRED) ON POGDESIGN")
        L.append("=" * 70)
        for r in extras:
            L.append(f"   {r.show.name}: {', '.join(r.extra_local)}")

    if rep.unmatched_local:
        L.append("")
        L.append("=" * 70)
        L.append("LOCAL SHOWS NOT FOUND ON POGDESIGN")
        L.append("(add an alias in aliases.json if the name differs on the site)")
        L.append("=" * 70)
        for name, n in sorted(rep.unmatched_local.items(), key=lambda kv: kv[0].lower()):
            L.append(f"   {name}  ({n} file(s))")

    L.append("")
    L.append("Show summary:")
    for r in sorted(rep.results, key=lambda r: r.show.name.lower()):
        local = f" <- local '{', '.join(sorted(r.local_names))}'" if r.local_names else ""
        L.append(f"   {r.show.name}{local}: {r.local_files} local file(s), "
                 f"{len(r.marked)} newly marked, {r.already_watched} already watched, "
                 f"{len(r.missing)} missing")
    return "\n".join(L) + "\n"


def load_aliases(path: str | Path) -> dict[str, str]:
    p = Path(path)
    if not p.is_file():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items() if not str(k).startswith("_")}


def run_sync(opts: Options, log=print, client: PogClient | None = None,
             today: _dt.date | None = None) -> SyncReport:
    today = today or _dt.date.today()
    client = client or PogClient(debug_dir=opts.debug_dir)
    if opts.debug_dir:
        log(f"Debug: saving every PoGDesign page to {opts.debug_dir}")
    report = SyncReport(dry_run=opts.dry_run)

    log("Scanning local folders...")
    local = scan(opts.folders, progress=log)
    report.local_files = len(local)
    if not local:
        log("No episode files found (expected names like 'Show - S01E02.mkv' or '1x02').")

    log("Logging in to PoGDesign...")
    client.login(opts.email, opts.password)
    log("Logged in.")

    catalogue = client.fetch_catalogue(progress=log)
    tracked = [s for s in catalogue.values() if s.tracked]
    log(f"Catalogue: {len(catalogue)} shows, {len(tracked)} in your filter.")

    matcher = ShowMatcher(catalogue, opts.aliases)
    by_show: dict[str, list[LocalEpisode]] = defaultdict(list)
    names: dict[str, set[str]] = defaultdict(set)
    unmatched: dict[str, int] = defaultdict(int)
    for le in local:
        show = matcher.match_any(le.show_candidates)
        if show:
            by_show[show.id].append(le)
            names[show.id].add(le.show_candidates[0])
        else:
            unmatched[le.show_candidates[0]] += 1
    report.unmatched_local = dict(unmatched)
    log(f"Matched {len(by_show)} local show(s) to PoGDesign; "
        f"{len(unmatched)} not found.")

    show_ids = list(by_show)
    if opts.include_tracked:
        show_ids += [s.id for s in tracked if s.id not in by_show]

    for i, sid in enumerate(show_ids, 1):
        show = catalogue[sid]
        res = ShowResult(show=show, local_files=len(by_show.get(sid, [])),
                         local_names=names.get(sid, set()))
        report.results.append(res)
        log(f"[{i}/{len(show_ids)}] {show.name}")
        try:
            episodes = client.fetch_episodes(show)
        except PogError as e:
            res.error = str(e)
            log(f"   error: {e}")
            continue

        have: set[tuple[int, int]] = set()
        for le in by_show.get(sid, []):
            for n in le.episodes:
                have.add((le.season, n))

        for ep in episodes:
            if ep.season == 0 and not opts.include_specials:
                continue
            if not ep.released(today):
                continue  # future / unaired: ignore entirely
            if (ep.season, ep.number) in have:
                if ep.watched:
                    res.already_watched += 1
                else:
                    res.marked.append(ep)
            elif not (ep.watched and opts.hide_watched_missing):
                res.missing.append(ep)

        released_keys = {(e.season, e.number) for e in episodes if e.released(today)}
        res.extra_local = [f"S{s:02d}E{n:02d}" for s, n in sorted(have - released_keys)
                           if s != 0 or opts.include_specials]

        if not opts.dry_run:
            done = []
            for ep in res.marked:
                try:
                    client.mark_watched(ep, show)
                except PogError as e:
                    res.error = f"failed marking {ep.code}: {e}"
                    log(f"   {res.error}")
                    break
                done.append(ep)
            res.marked = done
            if done:
                # Re-read the show page to make sure the ticks actually stuck.
                try:
                    now = {(e.season, e.number): e.watched for e in client.fetch_episodes(show)}
                    res.unconfirmed = [e for e in done if not now.get((e.season, e.number))]
                except PogError as e:
                    log(f"   could not verify ticks: {e}")
                if res.unconfirmed:
                    log(f"   WARNING: {len(res.unconfirmed)} of {len(done)} tick(s) "
                        "not confirmed by the site")
        if res.marked:
            log(f"   {'would mark' if opts.dry_run else 'marked'} {len(res.marked)} as watched")
        if res.missing:
            log(f"   {len(res.missing)} released episode(s) missing locally")
    return report
