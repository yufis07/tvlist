# TV List: sync your TV folders with PoGDesign

A small app that:

1. **Scans your folders** for TV episode files (`Show - S01E02.mkv`, `show.s01e02.720p.mp4`, `1x02`, multi-episode files like `S01E01E02` / `S01E01-E03`).
2. **Logs in to your [PoGDesign TV Calendar](https://www.pogdesign.co.uk/cat/) account** and **ticks every episode you have on disk as watched**. Episodes already ticked are left alone, and nothing is ever unticked.
3. **Lists the released episodes that are missing from your drive**, for the shows on your drive and (optionally) every show in your PoGDesign filter. Episodes that haven't aired yet are ignored.

It uses only the Python standard library, so there's nothing to install apart from Python 3.9 or newer.

## Run it

**Window (recommended):**

```
python -m tvlist
```

On Windows you can also double-click `run_tvlist.pyw`.

1. Enter your PoGDesign e-mail and password.
2. Click **Add folder…** and pick your TV folder(s).
3. Leave **Dry run** ticked for the first run. It shows what *would* be ticked without changing anything.
4. Click **Scan & Sync**, read the report, then untick **Dry run** and run it again to tick the episodes on the site.
5. **Save report…** writes the results to a text file.

**Command line:**

```
python -m tvlist --cli -f "D:\TV Shows" --dry-run
python -m tvlist --cli -f "D:\TV Shows" -f "E:\More TV" -e you@example.com
```

You'll be prompted for the password, or you can set the `POG_EMAIL` / `POG_PASSWORD` environment variables. Run `python -m tvlist --help` for all options:

| Option | Meaning |
|---|---|
| `--dry-run` | Preview only. Nothing is ticked on the site. |
| `--only-local` | Only check shows that are on your drive. Skip filtered shows that have no files. |
| `--specials` | Include season 0 / specials. |
| `--hide-watched-missing` | Don't list missing episodes you've already ticked on the site, for example ones you watched elsewhere. |
| `-o report.txt` | Where to save the report. The default is `tvlist-report-<date>.txt`. |

## Folder layouts it understands

```
TV/Show Name (2026)/Season 01/Show Name (2026) - S01E01.mkv
TV/Show Name/S01/episode.s01e01.mkv
TV/Show.Name.S01E01.720p.HDTV.mkv          (no show folder: uses the file name)
```

The show name comes from the show's folder (skipping `Season N` folders) or from the file name before `SxxEyy`. It's matched to PoGDesign while ignoring case, punctuation, a leading "The", `&` vs "and", years and country tags (US/UK). If two shows share a name, for example a 2001 show and its 2026 revival, the one in your filter wins.

If a show can't be matched, it shows up under **LOCAL SHOWS NOT FOUND ON POGDESIGN** in the report. Add a mapping to `~/.tvlist/aliases.json` (see `aliases.example.json`):

```json
{ "Shameless US": "Shameless (US)" }
```

## The report

* **MISSING RELEASED EPISODES**: aired episodes PoGDesign lists that aren't on your drive. Shows in your filter with no files at all are tagged `[no files on disk]`.
* **MARKED AS WATCHED**: what was ticked, or would be ticked in a dry run.
* **LOCAL EPISODES NOT LISTED ON POGDESIGN**: files whose episode number the site doesn't know or hasn't aired yet. This often points to a naming or numbering mismatch.
* **LOCAL SHOWS NOT FOUND ON POGDESIGN**: candidates for aliases.

## Troubleshooting

* After ticking, the app **re-reads each show's page to check the ticks stuck**. Any that didn't are listed under **TICKS NOT CONFIRMED**.
* The **Show summary** at the end of the report shows which local folder matched which PoGDesign show, for example `Scrubs (2026) <- local 'Scrubs'`. Check it for wrong matches.
* Tick **Save debug files** (or pass `--debug`) to save every page the site returns to `~/.tvlist/debug/<date-time>/`. These pages can include your e-mail address. Your password is never saved.

## Privacy

Your password is sent only to pogdesign.co.uk and is never saved. If you tick "Remember e-mail", the e-mail and folder list are stored in `~/.tvlist/settings.json`.

## How it talks to PoGDesign

PoGDesign has no public API. The app uses the same endpoints as the website's own pages: the login form, the `all-shows` and `show-select` pages to find the catalogue and your filter, each show's `-summary` page for its episode list, and `/cat/watchhandle` (the call made when you click an episode checkbox) to tick episodes. Requests are spaced out to be gentle on the site. If PoGDesign changes its HTML, the parsers in `tvlist/pogdesign.py` may need updating.

## Tests

```
python -m unittest discover -s tests
```
