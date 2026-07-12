# Guitar Hero → Neo4j Aura Loader

> **This is instructor / pre-class setup, not part of the lab.** Everything in
> this directory populates the shared Aura instance with the song/note graph
> *before* the session — lab participants don't need to run any of it. It's here
> for reference if you want to see how the dataset was built, or if you need
> to refresh/rebuild it. See the [top-level README](../README.md) for the lab
> itself.

Loads Clone Hero / Guitar Hero `.chart` songs into a Neo4j Aura database so
lab participants can run Neo4j's **FastPath** algorithm and find similar
songs based on their note sequences.

## Quick start

Once your venv is set up and `.env` is in place (see below), this reproduces
the shared dataset exactly — the tuning we landed on (300 songs, a mix of
well-known mainstream artists, 2.5–5 minute duration band) is baked into the
scripts' own defaults, so no extra flags are needed:

```bash
cd data
source .venv/bin/activate
python3 fetch_charts.py --count 300 --popular
python3 load_songs.py
```

That's the whole "easy button" for refreshing the lab environment before a
future session. If you're reloading into a database that already has data in
it (rather than starting fresh), add `--clear` to `load_songs.py` — see
"Running" below for why.

## Graph model

```
(:Song {song_id, title, duration, track, artist?, album?, genre?, year?, charter?})
(:Note {timestamp, button})

(:Song)-[:FIRST_NOTE]->(:Note)
(:Note)-[:NEXT_NOTE]->(:Note)-[:NEXT_NOTE]->(:Note)-> ...
(:Song)-[:LAST_NOTE]->(:Note)
```

- **Song**
  - `song_id` — stable unique key, derived from the song's folder path relative to `charts/` (e.g. `Bathory - Hades`). Used to `MERGE` songs.
  - `title`, `artist`, `album`, `genre`, `year`, `charter` — pulled from `song.ini` first, falling back to the `[Song]` section of the `.chart` file, then the folder name for `title`.
  - `duration` — seconds. Taken from `song.ini`'s `song_length` (ms) when present; otherwise estimated by walking the chart's own tempo map (`[SyncTrack]` `B` events) out to the last note.
  - `track` — which instrument/difficulty section was used (see below).
- **Note**
  - `timestamp` — seconds from the start of the song, computed from the tempo map (not raw ticks).
  - `button` — integer color code: `0`=green, `1`=red, `2`=yellow, `3`=blue, `4`=orange, `5`=open note. Chart "modifier" events (forced HOPO / tap flags) are not real notes and are skipped.
- Every note in a song's chart is chained in chronological order via `NEXT_NOTE`, starting from the `Song`'s `FIRST_NOTE` and ending at `LAST_NOTE`. This makes the whole note sequence traversable from the `Song` node, which is what makes it usable for FastPath.

The script picks one note track per song, in priority order: `ExpertSingle` → `HardSingle` → `MediumSingle` → `EasySingle` (lead guitar, GRYBO layout). It ignores other instruments (bass, drums, 6-fret, etc).

## Prerequisites

- Python 3.9+
- Aura API credentials with write access to the shared Aura instance used for the lab
- A local folder of Clone Hero-style song folders (see "Getting chart data")

Create a virtual environment and install this directory's dependencies into it
(separate from the lab notebook's own `requirements.txt` one level up — this
directory only needs the plain Neo4j driver, not `graphdatascience`):

**With [uv](https://docs.astral.sh/uv/)** (recommended — faster, and picks a
matching Python automatically):

```bash
cd data
uv venv
uv pip install -r requirements.txt --python .venv/bin/python
source .venv/bin/activate
```

**With plain `venv`:**

```bash
cd data
python3 -m venv .venv
source .venv/bin/activate
pip3 install -r requirements.txt
```

Either way, once the venv is active, run the scripts as `python3 load_songs.py ...` /
`python3 fetch_charts.py ...` from inside `data/`, as shown below. `.venv/` is
already in `.gitignore`.

## Setup

`.env` lives in the **repo root** (one level up from here), shared with the
lab notebook — see the [top-level README](../README.md) for how to set it up.
`load_songs.py` finds it automatically; no separate `.env` needed in this
directory.

Put chart files under `charts/` — it's gitignored (fan-made charts often use
copyrighted commercial audio, so they aren't committed to the repo), so
you'll need to fetch or download them yourself; see "Getting chart data"
below. The script recursively scans for any folder containing a `*.chart`
file, so both flat layouts and nested "pack" layouts work:

```
charts/
  Bathory - Hades/
    notes.chart
    song.ini
  Some Pack/
    Artist - Song A/
      notes.chart
      song.ini
    Artist - Song B/
      notes.chart
      song.ini
```

`song.ini` is optional but recommended — without it, metadata is pulled
from the `.chart` file itself (often less reliable) and duration is
estimated rather than read directly.

## Getting chart data

### Automated: `fetch_charts.py` (recommended for bulk)

Downloads charts straight into `charts/` from [Chorus Encore](https://enchor.us),
without clicking through the website one song at a time:

```bash
python3 fetch_charts.py --count 300
```

It uses the same backend the official [Bridge](https://github.com/Geomitron/Bridge)
desktop app uses (`api.enchor.us/search` + `files.enchor.us/<md5>.sng`). Charts
are stored server-side in the `.sng` container format, which bundles chart
data together with audio/video/images into one file. Rather than downloading
the whole thing, the script:

1. Range-GETs just enough of the `.sng` header to read its internal file index
2. Range-GETs only the `notes.chart` byte range out of that index
3. Reconstructs `song.ini` from the `.sng`'s embedded metadata

So each song costs a few KB of network traffic, not several MB — no audio is
ever downloaded. Charts that only ship `notes.mid` (no `notes.chart`) are
skipped, since `load_songs.py` doesn't parse MIDI.

By default `--query '*'` pulls whatever's newest/most relevant on Chorus,
which skews toward obscure fan charts. For a more recognizable set:

```bash
python3 fetch_charts.py --count 300 --popular
python3 fetch_charts.py --count 40 --artists "The Beatles,Queen,Nirvana"
```

`--popular` round-robins evenly across a built-in list of ~40 well-known
mainstream artists (spanning genres/decades) so no single artist dominates —
edit `POPULAR_ARTISTS` near the top of the script to change the list.
`--artists` does the same with your own comma-separated list instead. Both
filter results to an exact (case-insensitive) artist-name match, since a
plain text search for e.g. "Queen" also surfaces cover bands and unrelated
songs with "Queen" in the title.

| Flag | Description |
| --- | --- |
| `--count N` | Target number of songs to fetch (default: 300) |
| `--query STR` | Search string, or `*` for no filter (default: `*`) |
| `--popular` | Fetch from the built-in list of well-known artists instead of `--query`, round-robin |
| `--artists "A,B,C"` | Fetch from your own comma-separated artist list instead of `--query`, round-robin |
| `--instrument` | Only fetch charts with this instrument available (default: `guitar`, i.e. lead guitar/GRYBO) |
| `--difficulty` | Only fetch charts with this difficulty available (default: any) |
| `--min-minutes N` | Skip charts shorter than this many minutes — filters out preview/meme/broken stub charts. `0` disables (default: 2.5) |
| `--max-minutes N` | Skip charts longer than this many minutes — filters out "full album"/medley mega-charts, and keeps song lengths in a tight band so FastPath-style similarity isn't dominated by duration differences. `0` disables (default: 5) |
| `--out-dir DIR` | Output directory (default: `charts`) |
| `--skip-existing` | Skip songs whose output folder already exists |
| `--sleep SECONDS` | Delay between songs (default: 0.2) |
| `--dry-run` | Search and print results without downloading |

Chorus Encore is a free community project — `--sleep` (default 0.2s between
songs) keeps requests to a reasonable pace. Charts pulled this way are still
fan-made and often use copyrighted commercial audio, same caveat as below.

### Manual

Two more free sources, both link to community-made `.chart`/`.mid` files:

- [Chorus Encore](https://enchor.us) — largest aggregator, searchable, individual songs or full packs. When downloading manually, pick the **"Contains chart folder" (.zip)** format, not `.sng` — it extracts straight into the `notes.chart` + `song.ini` layout `load_songs.py` expects.
- [Custom Songs Central](https://customsongscentral.com/) — curated, themed packs.
- [Stickheadz32/Clone-Hero-custom-charts](https://github.com/Stickheadz32/Clone-Hero-custom-charts) — small (~43 songs), directly browsable on GitHub, no scraping needed.

Combine downloads from these into `charts/` to reach a few hundred songs.
Most fan-made charts use copyrighted commercial audio — fine for an internal
workshop, not for public redistribution.

## Running

Always sanity-check first with `--dry-run` (parses everything, prints what
would be loaded, never touches the database):

```bash
python3 load_songs.py --dry-run
```

Then load for real:

```bash
python3 load_songs.py
```

### Flags

| Flag | Description |
| --- | --- |
| `--charts-dir DIR` | Root folder to scan (default: `charts`) |
| `--limit N` | Only load the first N songs found — useful for a quick test load |
| `--max-notes-per-song N` | Truncate each song's note chain to N notes (see quota note below) |
| `--clear` | Delete all existing `Song`/`Note` nodes before loading |
| `--dry-run` | Parse and print only, no database writes |

The script is **not idempotent** — rerunning it without `--clear` will create
duplicate `Note` nodes for songs it already loaded (existing `Song` nodes are
matched and updated via `song_id`, but their notes are always freshly
created). Use `--clear` for a clean reload. Since lab participants query the
shared instance live during class, only rerun this against it well before
(or well after) a session, not while people are working.

### Aura node/relationship limits

A single Expert chart can have 1,000–5,000+ notes, each becoming a `Note`
node plus a `NEXT_NOTE` relationship. A few hundred songs can add up to
hundreds of thousands of nodes/relationships fast — Aura Free tier caps out
at 200k nodes / 400k relationships. Check your tier before a full load, or
use `--max-notes-per-song` to cap volume.

## Output

The script prints one line per song as it's loaded (or skipped, with a
reason — e.g. no supported note track found), and a summary line at the end:

```
[1/312] Loaded 'Hades' - 1673 notes
[2/312] SKIP 'Some Folder': no ExpertSingle/HardSingle/MediumSingle/EasySingle track found
...
Done. Loaded 311 songs (742,918 notes total), skipped 1.
```
