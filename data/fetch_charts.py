#!/usr/bin/env python3
"""
Fetch Clone Hero charts from Chorus Encore (enchor.us) straight into charts/,
without clicking through the website one song at a time.

Uses the same backend the official "Bridge" desktop app uses
(https://github.com/Geomitron/Bridge):
  - POST https://api.enchor.us/search  to find charts
  - GET  https://files.enchor.us/<md5>.sng  to fetch the chart data

Charts are stored server-side in the .sng container format (see
https://github.com/mdsitton/SngFileFormat). Rather than downloading full
.sng files (which bundle audio/video and can be many MB each), this script:
  1. Range-GETs just enough of the .sng header to read its file index
  2. Range-GETs only the notes.chart/notes.mid byte range out of that index
  3. Reconstructs song.ini from the .sng's embedded metadata

This means each song typically costs a few KB of network traffic instead
of several MB, and no audio is ever downloaded or stored.

Usage:
    python3 fetch_charts.py --count 300
    python3 fetch_charts.py --count 50 --query "metal" --dry-run
    python3 fetch_charts.py --count 200 --popular
    python3 fetch_charts.py --count 40 --artists "The Beatles,Queen,Nirvana"

Output folders land in --out-dir (default: charts), matching the layout
load_songs.py already expects.
"""

import argparse
import json
import re
import ssl
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path

import certifi

API_SEARCH_URL = "https://api.enchor.us/search"
FILES_BASE_URL = "https://files.enchor.us"
USER_AGENT = "GuitarHeroWorkshopChartFetcher/1.0"
SNG_IDENTIFIER = b"SNGPKG"
PREFIX_START_SIZE = 65536
PREFIX_MAX_SIZE = 8 * 1024 * 1024
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Uses certifi's bundled CA certs rather than relying on the local Python
# install's own trust store, which is frequently misconfigured on macOS
# (python.org installs and some conda environments don't wire up the
# system keychain, causing CERTIFICATE_VERIFY_FAILED on any HTTPS request).
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

# A broad, recognizable spread across genres/decades so a workshop audience
# is likely to know at least a few of these. Feel free to edit this list.
POPULAR_ARTISTS = [
    "The Beatles", "Queen", "Michael Jackson", "Taylor Swift", "Ed Sheeran",
    "Adele", "Elton John", "ABBA", "Fleetwood Mac", "Nirvana", "Metallica",
    "AC/DC", "Guns N' Roses", "Foo Fighters", "Green Day", "Bon Jovi",
    "Journey", "Eagles", "U2", "Red Hot Chili Peppers", "Linkin Park",
    "Imagine Dragons", "Maroon 5", "Billie Eilish", "Dua Lipa",
    "The Rolling Stones", "Led Zeppelin", "Pink Floyd", "David Bowie",
    "Prince", "Whitney Houston", "Stevie Wonder", "Bruno Mars", "Katy Perry",
    "Rihanna", "Madonna", "Coldplay", "The Weeknd", "Beyonce", "Daft Punk",
    "Radiohead", "Eminem", "Justin Bieber", "Ariana Grande",
]


class SkipSong(Exception):
    pass


def http_post_json(url, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
        return json.loads(resp.read())


def http_range_get(url, start, end):
    req = urllib.request.Request(
        url,
        headers={"Range": f"bytes={start}-{end}", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
        return resp.read()


def search_page(query, page, per_page, instrument, difficulty):
    body = {
        "search": query,
        "per_page": per_page,
        "page": page,
        "instrument": instrument,
        "difficulty": difficulty,
        "drumType": None,
        "drumsReviewed": True,
        "sort": None,
        "source": "bridge",
    }
    return http_post_json(API_SEARCH_URL, body)


def iter_search_results(query, instrument, difficulty, per_page=100):
    """Yields every result for a search query, paging until exhausted."""
    page = 1
    while True:
        try:
            response = search_page(query, page, per_page, instrument, difficulty)
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            print(f"Search request failed for '{query}': {exc}")
            return
        results = response.get("data", [])
        if not results:
            return
        yield from results
        found = response.get("found", 0)
        if page * per_page >= found:
            return
        page += 1


def iter_artist_results(artist, instrument, difficulty, per_page=100):
    """Yields only results whose artist field exactly matches (case-insensitive),
    since a plain text search also picks up covers/tributes/unrelated matches."""
    target = artist.strip().lower()
    for entry in iter_search_results(artist, instrument, difficulty, per_page):
        if (entry.get("artist") or "").strip().lower() == target:
            yield entry


def round_robin(iterators):
    """Pulls one item at a time from each iterator in turn, for even coverage
    across artists instead of exhausting the first one's whole catalog first."""
    active = list(iterators)
    while active:
        next_active = []
        for it in active:
            try:
                yield next(it)
                next_active.append(it)
            except StopIteration:
                pass
        active = next_active


def try_parse_sng_prefix(buf):
    """Attempt to parse the header/metadata/file-index sections out of buf.
    Returns a dict on success, or None if buf doesn't yet contain enough bytes."""
    if len(buf) < 26:
        return None
    if buf[0:6] != SNG_IDENTIFIER:
        raise SkipSong("not a valid .sng file (bad identifier)")
    pos = 26  # 6 (identifier) + 4 (version) + 16 (xorMask)
    xor_mask = buf[10:26]

    if len(buf) < pos + 8:
        return None
    metadata_len = struct.unpack_from("<Q", buf, pos)[0]
    metadata_section_end = pos + 8 + metadata_len
    if len(buf) < metadata_section_end:
        return None
    pos += 8
    metadata_count = struct.unpack_from("<Q", buf, pos)[0]
    pos += 8
    metadata = {}
    for _ in range(metadata_count):
        if pos + 4 > len(buf):
            return None
        key_len = struct.unpack_from("<i", buf, pos)[0]
        pos += 4
        if pos + key_len > len(buf):
            return None
        key = buf[pos:pos + key_len].decode("utf-8")
        pos += key_len
        if pos + 4 > len(buf):
            return None
        val_len = struct.unpack_from("<i", buf, pos)[0]
        pos += 4
        if pos + val_len > len(buf):
            return None
        value = buf[pos:pos + val_len].decode("utf-8")
        pos += val_len
        metadata[key] = value
    pos = metadata_section_end

    if len(buf) < pos + 8:
        return None
    file_meta_len = struct.unpack_from("<Q", buf, pos)[0]
    fileindex_section_end = pos + 8 + file_meta_len
    if len(buf) < fileindex_section_end:
        return None
    pos += 8
    file_count = struct.unpack_from("<Q", buf, pos)[0]
    pos += 8
    files = []
    for _ in range(file_count):
        if pos + 1 > len(buf):
            return None
        fname_len = buf[pos]
        pos += 1
        if pos + fname_len > len(buf):
            return None
        fname = buf[pos:pos + fname_len].decode("utf-8")
        pos += fname_len
        if pos + 16 > len(buf):
            return None
        contents_len = struct.unpack_from("<Q", buf, pos)[0]
        pos += 8
        contents_index = struct.unpack_from("<Q", buf, pos)[0]
        pos += 8
        files.append({"name": fname, "length": contents_len, "offset": contents_index})
    pos = fileindex_section_end

    if len(buf) < pos + 8:
        return None

    return {"xor_mask": xor_mask, "metadata": metadata, "files": files}


def fetch_sng_index(md5):
    url = f"{FILES_BASE_URL}/{md5}.sng"
    size = PREFIX_START_SIZE
    while True:
        try:
            buf = http_range_get(url, 0, size - 1)
        except urllib.error.HTTPError as exc:
            raise SkipSong(f"HTTP {exc.code} fetching .sng header")
        parsed = try_parse_sng_prefix(buf)
        if parsed is not None:
            return parsed
        if len(buf) < size:
            raise SkipSong(".sng file truncated or malformed (couldn't parse file index)")
        if size >= PREFIX_MAX_SIZE:
            raise SkipSong(f".sng file index larger than {PREFIX_MAX_SIZE} bytes, giving up")
        size *= 2


def unmask(buf, xor_mask):
    out = bytearray(len(buf))
    for i in range(len(buf)):
        out[i] = buf[i] ^ xor_mask[i % 16] ^ (i & 0xFF)
    return bytes(out)


def fetch_chart_text(md5, file_entry):
    url = f"{FILES_BASE_URL}/{md5}.sng"
    start = file_entry["offset"]
    end = start + file_entry["length"] - 1
    try:
        raw = http_range_get(url, start, end)
    except urllib.error.HTTPError as exc:
        raise SkipSong(f"HTTP {exc.code} fetching {file_entry['name']}")
    return raw


def pick_chart_file(files):
    by_name = {f["name"].lower(): f for f in files}
    if "notes.chart" in by_name:
        return by_name["notes.chart"]
    return None  # notes.mid is out of scope for load_songs.py today


def sanitize_folder_name(name):
    name = INVALID_FILENAME_CHARS.sub("-", name).strip().rstrip(".")
    return name or "untitled"


def write_song_ini(path, metadata):
    lines = ["[Song]"]
    for key, value in metadata.items():
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def base_folder_name(entry):
    title = entry.get("name") or "Untitled"
    artist = entry.get("artist") or "Unknown Artist"
    return sanitize_folder_name(f"{artist} - {title}")


def check_duration(metadata, min_minutes, max_minutes):
    """Authoritative duration check against the .sng's own embedded metadata,
    since the search API's pre-fetch song_length is sometimes missing and would
    otherwise let a candidate through the pre-fetch filter unchecked."""
    if not min_minutes and not max_minutes:
        return
    raw = metadata.get("song_length")
    if raw is None:
        raise SkipSong("no song_length in .sng metadata, can't verify duration")
    try:
        minutes = int(raw) / 60000
    except ValueError:
        raise SkipSong(f"unparseable song_length in .sng metadata: {raw!r}")
    if min_minutes and minutes < min_minutes:
        raise SkipSong(f"song is {minutes:.1f} min, shorter than --min-minutes {min_minutes}")
    if max_minutes and minutes > max_minutes:
        raise SkipSong(f"song is {minutes:.1f} min, longer than --max-minutes {max_minutes}")


def fetch_one_song(entry, out_dir, used_names, skip_existing=False, min_minutes=None, max_minutes=None):
    base_name = base_folder_name(entry)
    if skip_existing and base_name in used_names:
        raise SkipSong(f"'{base_name}' already exists in {out_dir}")

    md5 = entry["md5"]
    index = fetch_sng_index(md5)
    xor_mask = index["xor_mask"]

    check_duration(index["metadata"], min_minutes, max_minutes)

    chart_file = pick_chart_file(index["files"])
    if chart_file is None:
        raise SkipSong("no notes.chart in this .sng (likely notes.mid only, unsupported)")

    raw = fetch_chart_text(md5, chart_file)
    chart_bytes = unmask(raw, xor_mask)

    folder_name = base_name
    if folder_name in used_names:
        folder_name = sanitize_folder_name(f"{base_name} ({entry['chartId']})")
    used_names.add(folder_name)

    folder = out_dir / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "notes.chart").write_bytes(chart_bytes)
    write_song_ini(folder / "song.ini", index["metadata"])

    return folder_name


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=300, help="Target number of songs to fetch (default: 300)")
    parser.add_argument("--query", default="*", help="Search string, or '*' for no filter (default: *)")
    parser.add_argument("--popular", action="store_true",
                         help="Fetch from a built-in list of well-known mainstream artists instead of --query, "
                              "round-robining across artists for even coverage")
    parser.add_argument("--artists", default=None,
                         help="Comma-separated artist names to fetch from instead of --query (implies --popular's "
                              "round-robin behavior, but with your own list)")
    parser.add_argument("--instrument", default="guitar",
                         choices=["guitar", "guitarcoop", "rhythm", "bass", "drums", "keys",
                                  "guitarghl", "guitarcoopghl", "rhythmghl", "bassghl"],
                         help="Only fetch charts with this instrument available (default: guitar)")
    parser.add_argument("--difficulty", default=None, choices=["expert", "hard", "medium", "easy"],
                         help="Only fetch charts with this difficulty available for --instrument (default: any)")
    parser.add_argument("--min-minutes", type=float, default=2.5,
                         help="Skip charts shorter than this many minutes - filters out preview/meme/broken "
                              "stub charts. 0 disables (default: 2.5)")
    parser.add_argument("--max-minutes", type=float, default=5.0,
                         help="Skip charts longer than this many minutes - filters out 'full album'/medley "
                              "mega-charts, and keeps song lengths in a tight band so FastPath-style similarity "
                              "isn't dominated by duration differences. 0 disables (default: 5)")
    parser.add_argument("--out-dir", default="charts", help="Output directory (default: charts)")
    parser.add_argument("--sleep", type=float, default=0.2, help="Seconds to wait between songs (default: 0.2)")
    parser.add_argument("--skip-existing", action="store_true",
                         help="Skip songs whose output folder already exists")
    parser.add_argument("--dry-run", action="store_true", help="Search and print results without downloading")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = script_dir / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    used_names = {p.name for p in out_dir.iterdir() if p.is_dir()} if out_dir.exists() else set()

    if args.artists or args.popular:
        artist_list = [a.strip() for a in args.artists.split(",") if a.strip()] if args.artists else POPULAR_ARTISTS
        preview = ", ".join(artist_list[:6]) + ("..." if len(artist_list) > 6 else "")
        print(f"Fetching from {len(artist_list)} artists (round-robin): {preview}")
        candidates = round_robin(
            iter_artist_results(artist, args.instrument, args.difficulty) for artist in artist_list
        )
    else:
        candidates = iter_search_results(args.query, args.instrument, args.difficulty)

    fetched, skipped = 0, 0
    seen_chart_ids = set()
    seen_songs = set()  # (artist, title) lowercased, so different charters' versions of the same song don't eat multiple slots

    for entry in candidates:
        if fetched >= args.count:
            break
        chart_id = entry["chartId"]
        if chart_id in seen_chart_ids:
            continue
        seen_chart_ids.add(chart_id)

        # Fast pre-fetch filter using the search API's metadata, when present -- this just
        # avoids wasting a download on an obviously out-of-range candidate. It's not the
        # authoritative check: song_length is sometimes missing here, in which case this
        # simply doesn't filter, and fetch_one_song()'s post-download check (against the
        # .sng's own embedded metadata) is what actually enforces the bounds.
        song_length_ms = entry.get("song_length")
        if song_length_ms:
            minutes = song_length_ms / 60000
            if args.max_minutes and minutes > args.max_minutes:
                continue
            if args.min_minutes and minutes < args.min_minutes:
                continue

        song_key = ((entry.get("artist") or "").strip().lower(), (entry.get("name") or "").strip().lower())
        if song_key in seen_songs:
            continue
        seen_songs.add(song_key)

        label = f"{entry.get('artist') or '?'} - {entry.get('name') or '?'}"

        if args.dry_run:
            print(f"[{fetched + skipped + 1}] {label} (md5={entry['md5']})")
            fetched += 1
            continue

        try:
            folder_name = fetch_one_song(
                entry, out_dir, used_names, args.skip_existing,
                min_minutes=args.min_minutes, max_minutes=args.max_minutes,
            )
        except SkipSong as exc:
            print(f"SKIP '{label}': {exc}")
            skipped += 1
            continue
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            print(f"SKIP '{label}': network error: {exc}")
            skipped += 1
            continue

        fetched += 1
        print(f"[{fetched}/{args.count}] Fetched '{folder_name}'")
        time.sleep(args.sleep)

    print(f"\nDone. Fetched {fetched} songs, skipped {skipped}.")


if __name__ == "__main__":
    main()
