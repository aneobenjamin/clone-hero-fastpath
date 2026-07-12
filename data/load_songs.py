#!/usr/bin/env python3
"""
Load Clone Hero / Guitar Hero .chart songs into a Neo4j Aura database.

Graph model:
  (:Song {song_id, title, duration, artist?, album?, genre?, year?, charter?, track})
  (:Note {timestamp, button})
  (:Song)-[:FIRST_NOTE]->(:Note)          the earliest note in the song
  (:Note)-[:NEXT_NOTE]->(:Note)           chronological chain of every note

`timestamp` is seconds from the start of the song (computed from the chart's
own tempo map, not raw ticks). `button` is an integer color code: 0=green,
1=red, 2=yellow, 3=blue, 4=orange, 5=open.

Usage:
    python3 load_songs.py [--charts-dir charts] [--limit N]
                           [--max-notes-per-song N] [--clear] [--dry-run]

Expects a .env file in the repo root (one directory up from this script --
see ../.env.example) with NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, and
optionally NEO4J_DATABASE.

Each song is a directory anywhere under --charts-dir that contains a
*.chart file (optionally alongside a song.ini). This matches the folder
layout used by Chorus Encore, Custom Songs Central, and most GitHub chart
repos, including nested "pack" subfolders.
"""

import argparse
import bisect
import configparser
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

TRACK_PRIORITY = ["ExpertSingle", "HardSingle", "MediumSingle", "EasySingle"]
DEFAULT_RESOLUTION = 192
DEFAULT_BPM = 120.0


def read_text_safely(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def pick_chart_file(filenames):
    lower_map = {f.lower(): f for f in filenames}
    if "notes.chart" in lower_map:
        return lower_map["notes.chart"]

    def rank(f):
        lf = f.lower()
        if "[y]" in lf or "(y)" in lf or "[f]" in lf or "(f)" in lf:
            return 0
        if "[n]" in lf or "(n)" in lf:
            return 2
        return 1

    return sorted(filenames, key=rank)[0]


def find_song_folders(root: Path):
    for dirpath, _dirnames, filenames in os.walk(root):
        chart_files = [f for f in filenames if f.lower().endswith(".chart")]
        if chart_files:
            folder = Path(dirpath)
            yield folder, folder / pick_chart_file(chart_files)


def parse_chart_sections(text: str):
    sections = {}
    current = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections[current] = []
            continue
        if line in ("{", "}"):
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def parse_named_entries(lines):
    out = {}
    for line in lines:
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        out[key] = value
    return out


def parse_sync_track(lines):
    tempos = {}
    for line in lines:
        if "=" not in line:
            continue
        tick_str, _, rest = line.partition("=")
        parts = rest.strip().split()
        if len(parts) < 2 or parts[0] != "B":
            continue
        try:
            tick = int(tick_str.strip())
            bpm = int(parts[1]) / 1000.0
        except ValueError:
            continue
        if bpm > 0:
            tempos[tick] = bpm
    if 0 not in tempos:
        tempos[0] = DEFAULT_BPM
    return sorted(tempos.items())


def build_tick_to_seconds(tempos, resolution):
    checkpoints = []
    cumulative = 0.0
    prev_tick, prev_bpm = tempos[0]
    checkpoints.append((prev_tick, prev_bpm, cumulative))
    for tick, bpm in tempos[1:]:
        cumulative += (tick - prev_tick) / resolution * (60.0 / prev_bpm)
        checkpoints.append((tick, bpm, cumulative))
        prev_tick, prev_bpm = tick, bpm
    ticks = [c[0] for c in checkpoints]

    def tick_to_seconds(tick):
        idx = max(bisect.bisect_right(ticks, tick) - 1, 0)
        base_tick, bpm, base_seconds = checkpoints[idx]
        return base_seconds + (tick - base_tick) / resolution * (60.0 / bpm)

    return tick_to_seconds


def parse_notes(lines):
    raw_notes = []
    max_tick = 0
    for line in lines:
        if "=" not in line:
            continue
        tick_str, _, rest = line.partition("=")
        parts = rest.strip().split()
        if len(parts) < 2 or parts[0] != "N":
            continue
        try:
            tick = int(tick_str.strip())
            fret = int(parts[1])
        except ValueError:
            continue
        if fret not in (0, 1, 2, 3, 4, 7):
            continue  # 5=forced/6=tap are modifiers on an existing note, not notes themselves
        button = 5 if fret == 7 else fret
        raw_notes.append((tick, button))
        max_tick = max(max_tick, tick)
    raw_notes.sort(key=lambda n: (n[0], n[1]))
    return raw_notes, max_tick


def pick_note_track(sections):
    for name in TRACK_PRIORITY:
        if sections.get(name):
            return name, sections[name]
    return None, None


def parse_song_ini(path: Path):
    if not path.exists():
        return {}
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read_string(read_text_safely(path))
    except configparser.Error:
        return {}
    section_name = next((s for s in parser.sections() if s.lower() == "song"), None)
    if section_name is None:
        return {}
    return dict(parser.items(section_name))


def parse_song_folder(folder: Path, chart_path: Path, song_id: str, max_notes_per_song=None):
    sections = parse_chart_sections(read_text_safely(chart_path))
    if "Song" not in sections:
        raise ValueError("missing [Song] section")

    chart_meta = parse_named_entries(sections["Song"])
    try:
        resolution = int(chart_meta.get("Resolution", DEFAULT_RESOLUTION))
    except ValueError:
        resolution = DEFAULT_RESOLUTION
    if resolution <= 0:
        resolution = DEFAULT_RESOLUTION

    ini_meta = parse_song_ini(folder / "song.ini")

    track_name, track_lines = pick_note_track(sections)
    if not track_lines:
        raise ValueError("no ExpertSingle/HardSingle/MediumSingle/EasySingle track found")

    notes, max_note_tick = parse_notes(track_lines)
    if not notes:
        raise ValueError("note track has no playable notes")
    if max_notes_per_song:
        notes = notes[:max_notes_per_song]

    tempos = parse_sync_track(sections.get("SyncTrack", []))
    tick_to_seconds = build_tick_to_seconds(tempos, resolution)

    title = (ini_meta.get("name") or chart_meta.get("Name") or folder.name).strip()
    artist = (ini_meta.get("artist") or chart_meta.get("Artist") or "").strip() or None
    album = (ini_meta.get("album") or chart_meta.get("Album") or "").strip() or None
    genre = (ini_meta.get("genre") or chart_meta.get("Genre") or "").strip() or None
    charter = (ini_meta.get("charter") or chart_meta.get("Charter") or "").strip() or None
    year = (ini_meta.get("year") or chart_meta.get("Year") or "").strip().lstrip(", ").strip() or None

    duration = None
    if ini_meta.get("song_length"):
        try:
            duration = round(int(ini_meta["song_length"]) / 1000.0, 3)
        except ValueError:
            duration = None
    if duration is None:
        duration = round(tick_to_seconds(max_note_tick), 3)

    song_props = {
        "song_id": song_id,
        "title": title,
        "duration": duration,
        "track": track_name,
    }
    for key, value in (("artist", artist), ("album", album), ("genre", genre), ("charter", charter), ("year", year)):
        if value:
            song_props[key] = value

    note_dicts = [
        {"idx": i, "timestamp": round(tick_to_seconds(tick), 3), "button": button}
        for i, (tick, button) in enumerate(notes)
    ]

    return song_props, note_dicts


LOAD_SONG_QUERY = """
MERGE (s:Song {song_id: $song_props.song_id})
SET s = $song_props
WITH s
UNWIND $notes AS note
CREATE (n:Note {timestamp: note.timestamp, button: note.button})
WITH s, n, note.idx AS idx
ORDER BY idx
WITH s, collect(n) AS noteNodes
WITH s, noteNodes, head(noteNodes) AS firstNote, noteNodes[-1] AS lastNote
MERGE (s)-[:FIRST_NOTE]->(firstNote)
MERGE (s)-[:LAST_NOTE]->(lastNote)
WITH noteNodes
UNWIND range(0, size(noteNodes) - 2) AS i
WITH noteNodes[i] AS a, noteNodes[i + 1] AS b
CREATE (a)-[:NEXT_NOTE]->(b)
"""

CONSTRAINT_QUERIES = [
    "CREATE CONSTRAINT song_id_unique IF NOT EXISTS FOR (s:Song) REQUIRE s.song_id IS UNIQUE",
    "CREATE INDEX song_title_index IF NOT EXISTS FOR (s:Song) ON (s.title)",
]

CLEAR_QUERY = "MATCH (n) WHERE n:Song OR n:Note DETACH DELETE n"


def load_into_neo4j(driver, database, song_props, notes):
    with driver.session(database=database) as session:
        session.run(LOAD_SONG_QUERY, song_props=song_props, notes=notes).consume()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--charts-dir", default="charts", help="Root directory to search for song folders (default: charts)")
    parser.add_argument("--limit", type=int, default=None, help="Only load the first N songs found (for quick testing)")
    parser.add_argument("--max-notes-per-song", type=int, default=None, help="Truncate each song's note chain to this many notes (helps stay under Aura node/relationship quotas)")
    parser.add_argument("--clear", action="store_true", help="Delete all existing Song/Note nodes before loading")
    parser.add_argument("--dry-run", action="store_true", help="Parse charts and print what would be loaded, without touching Neo4j")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    # .env lives in the repo root (one level up), shared with the lab notebook.
    load_dotenv(script_dir.parent / ".env")

    charts_dir = Path(args.charts_dir)
    if not charts_dir.is_absolute():
        charts_dir = script_dir / charts_dir
    if not charts_dir.is_dir():
        sys.exit(f"Charts directory not found: {charts_dir}")

    song_folders = sorted(find_song_folders(charts_dir), key=lambda pair: pair[0].as_posix())
    if not song_folders:
        sys.exit(f"No *.chart files found under {charts_dir}")
    if args.limit:
        song_folders = song_folders[: args.limit]

    driver = None
    if not args.dry_run:
        uri = os.environ.get("NEO4J_URI")
        username = os.environ.get("NEO4J_USERNAME")
        password = os.environ.get("NEO4J_PASSWORD")
        database = os.environ.get("NEO4J_DATABASE", "neo4j")
        if not all([uri, username, password]):
            sys.exit("Missing NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD. Copy .env.example to .env and fill it in.")
        driver = GraphDatabase.driver(uri, auth=(username, password))
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            if args.clear:
                print("Clearing existing Song/Note nodes...")
                session.run(CLEAR_QUERY).consume()
            for query in CONSTRAINT_QUERIES:
                session.run(query).consume()

    loaded, skipped = 0, 0
    total_notes = 0
    for i, (folder, chart_path) in enumerate(song_folders, start=1):
        rel = folder.relative_to(charts_dir)
        try:
            song_props, notes = parse_song_folder(folder, chart_path, rel.as_posix(), args.max_notes_per_song)
        except ValueError as exc:
            print(f"[{i}/{len(song_folders)}] SKIP '{rel}': {exc}")
            skipped += 1
            continue

        if args.dry_run:
            print(f"[{i}/{len(song_folders)}] '{song_props['title']}' - {len(notes)} notes, {song_props['duration']}s")
        else:
            load_into_neo4j(driver, os.environ.get("NEO4J_DATABASE", "neo4j"), song_props, notes)
            print(f"[{i}/{len(song_folders)}] Loaded '{song_props['title']}' - {len(notes)} notes")
        loaded += 1
        total_notes += len(notes)

    if driver:
        driver.close()

    print(f"\nDone. Loaded {loaded} songs ({total_notes} notes total), skipped {skipped}.")


if __name__ == "__main__":
    main()
