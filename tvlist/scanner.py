"""Find TV episode files on local drives and work out show / season / episode."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts", ".webm",
    ".mpg", ".mpeg", ".flv", ".divx", ".xvid", ".3gp", ".ogm", ".rmvb", ".iso",
}

# S01E02, s1e2, S01.E02, S01E02E03, S01E02-E03, S01E02-03
_SXE_RE = re.compile(
    r"(?<![a-z0-9])s(\d{1,2})[ ._-]?e(\d{1,3})((?:[ ._-]?-?[ ._-]?e?\d{1,3}(?![0-9p]))*)",
    re.I,
)
# 1x02, 01x02-03
_NX_RE = re.compile(r"(?<![a-z0-9])(\d{1,2})x(\d{2,3})((?:-\d{2,3})*)(?![0-9p])", re.I)
_EXTRA_NUM_RE = re.compile(r"(-)?\s*e?(\d{1,3})", re.I)

_SEASON_DIR_RE = re.compile(r"^(season|series|staffel|saison|temporada|s)[ ._-]*\d{1,3}$|^specials?$", re.I)
_YEAR_RE = re.compile(r"[\(\[]?\b(19\d{2}|20\d{2})\b[\)\]]?")


@dataclass(frozen=True)
class LocalEpisode:
    path: str
    show_candidates: tuple[str, ...]  # best guess first
    season: int
    episodes: tuple[int, ...]


def _clean_name(raw: str) -> str:
    s = re.sub(r"[._]+", " ", raw)
    s = re.sub(r"\s*[-–]\s*$", "", s.strip())
    s = re.sub(r"\s+", " ", s)
    return s.strip(" -[](")


def parse_episode_numbers(name: str) -> tuple[int, tuple[int, ...], int] | None:
    """Return (season, episodes, match_start) for a file name, or None."""
    m = _SXE_RE.search(name)
    if m:
        season, first, rest = int(m.group(1)), int(m.group(2)), m.group(3)
    else:
        m = _NX_RE.search(name)
        if not m:
            return None
        season, first, rest = int(m.group(1)), int(m.group(2)), m.group(3)
    eps = [first]
    extras = _EXTRA_NUM_RE.findall(rest or "")
    if len(extras) == 1 and extras[0][0] == "-" and int(extras[0][1]) > first:
        # Range like E01-E03 / E01-03 -> 1,2,3
        eps = list(range(first, int(extras[0][1]) + 1))
    else:
        for _, n in extras:
            n = int(n)
            if n not in eps and 0 < n - eps[-1] <= 5:
                eps.append(n)
    return season, tuple(eps), m.start()


def _show_folder(file_path: Path, root: Path) -> str | None:
    """Name of the show folder (skipping "Season 1" style folders), if any."""
    parent = file_path.parent
    while parent != root and _SEASON_DIR_RE.match(parent.name.strip()):
        parent = parent.parent
    return None if parent == root else parent.name


def scan(roots: list[str], progress=None) -> list[LocalEpisode]:
    """Walk the given folders and return every recognisable episode file."""
    found: list[LocalEpisode] = []
    for root_str in roots:
        root = Path(root_str).expanduser().resolve()
        if not root.is_dir():
            if progress:
                progress(f"Warning: folder not found: {root}")
            continue
        count = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                p = Path(dirpath) / fn
                if p.suffix.lower() not in VIDEO_EXTENSIONS or "sample" in fn.lower():
                    continue
                parsed = parse_episode_numbers(p.stem)
                if not parsed:
                    continue
                season, eps, start = parsed
                candidates: list[str] = []
                folder = _show_folder(p, root)
                if folder:
                    candidates.append(_clean_name(folder))
                prefix = _clean_name(p.stem[:start])
                if prefix and prefix.lower() not in (c.lower() for c in candidates):
                    candidates.append(prefix)
                if not folder and _clean_name(root.name) not in candidates:
                    # The scan root itself may be the show folder.
                    candidates.append(_clean_name(root.name))
                found.append(LocalEpisode(str(p), tuple(candidates), season, eps))
                count += 1
        if progress:
            progress(f"Found {count} episode files in {root}")
    return found
