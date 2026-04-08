#!/usr/bin/env python3
"""
Scan music collections and sync to SQLite database.
Reads FLAC metadata via mutagen. Optionally enriches via MusicBrainz.

Usage:
    python scan.py              # scan both collections
    python scan.py --musicbrainz  # also query MusicBrainz for missing fields
"""

import os
import sqlite3
import argparse
import time

from mutagen.flac import FLAC
from mutagen import MutagenError

from config import COLLECTION_1_PATH, COLLECTION_2_PATH, DB_PATH

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS artists (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    mbid TEXT,
    UNIQUE(name COLLATE NOCASE)
);

CREATE TABLE IF NOT EXISTS albums (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_id  INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    label      TEXT,
    genre      TEXT,
    year       INTEGER,
    path       TEXT NOT NULL UNIQUE,
    collection INTEGER NOT NULL CHECK(collection IN (1, 2)),
    mbid       TEXT
);

CREATE INDEX IF NOT EXISTS idx_albums_collection ON albums(collection);
CREATE INDEX IF NOT EXISTS idx_albums_artist_id  ON albums(artist_id);
"""


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------

def _tag(audio, *keys):
    """Return the first non-empty tag value from a mutagen FLAC object."""
    for key in keys:
        for variant in (key.upper(), key.lower(), key.title()):
            val = audio.get(variant)
            if val:
                return str(val[0]).strip()
    return None


def read_flac_metadata(album_path):
    """Read album metadata from the first FLAC file found in album_path."""
    meta = dict(artist=None, album=None, label=None, genre=None, year=None)
    try:
        flac_files = sorted(
            f for f in os.listdir(album_path) if f.lower().endswith(".flac")
        )
        if not flac_files:
            return meta
        audio = FLAC(os.path.join(album_path, flac_files[0]))
        meta["artist"] = _tag(audio, "albumartist", "artist")
        meta["album"]  = _tag(audio, "album")
        meta["label"]  = _tag(audio, "organization", "label", "publisher")
        meta["genre"]  = _tag(audio, "genre")
        raw_date = _tag(audio, "date", "year")
        if raw_date:
            meta["year"] = int(raw_date[:4])
    except (MutagenError, OSError, ValueError):
        pass
    return meta


def folder_fallback(album_path, collection_root):
    """Derive artist/album names from folder structure when tags are missing."""
    rel = os.path.relpath(album_path, collection_root)
    parts = rel.split(os.sep)
    if len(parts) >= 2:
        return parts[-2], parts[-1]   # Artist/Album
    return None, parts[0]             # flat: just album name


# ---------------------------------------------------------------------------
# MusicBrainz (optional)
# ---------------------------------------------------------------------------

def mb_enrich(artist_name, album_title):
    """Query MusicBrainz for label, year, and release mbid."""
    try:
        import musicbrainzngs
        result = musicbrainzngs.search_releases(
            artist=artist_name, release=album_title, limit=1
        )
        releases = result.get("release-list", [])
        if not releases:
            return {}
        rel = releases[0]
        data = {"mbid": rel.get("id")}
        date = rel.get("date", "")
        if len(date) >= 4:
            try:
                data["year"] = int(date[:4])
            except ValueError:
                pass
        labels = rel.get("label-info-list", [])
        if labels:
            lbl = labels[0].get("label", {}).get("name")
            if lbl:
                data["label"] = lbl
        return data
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def find_album_dirs(collection_path):
    """Yield directories that directly contain at least one FLAC file."""
    for root, dirs, files in os.walk(collection_path):
        dirs.sort()
        if any(f.lower().endswith(".flac") for f in files):
            yield root
            dirs.clear()   # don't recurse deeper once album folder found


def upsert_artist(conn, name):
    conn.execute(
        "INSERT INTO artists(name) VALUES(?) ON CONFLICT(name) DO NOTHING", (name,)
    )
    return conn.execute(
        "SELECT id FROM artists WHERE name=? COLLATE NOCASE", (name,)
    ).fetchone()[0]


def scan_collection(conn, collection_path, collection_num, use_mb=False):
    """Scan a collection folder and upsert all albums into the DB."""
    seen_paths = set()

    for album_path in find_album_dirs(collection_path):
        meta = read_flac_metadata(album_path)

        # Fill missing artist / album from folder names
        if not meta["artist"] or not meta["album"]:
            fb_artist, fb_album = folder_fallback(album_path, collection_path)
            meta["artist"] = meta["artist"] or fb_artist or "Unknown Artist"
            meta["album"]  = meta["album"]  or fb_album  or "Unknown Album"

        # Optional MusicBrainz enrichment (throttled to 1 req/s)
        if use_mb:
            mb = mb_enrich(meta["artist"], meta["album"])
            for key, val in mb.items():
                if not meta.get(key):
                    meta[key] = val
            time.sleep(1.1)

        artist_id = upsert_artist(conn, meta["artist"])

        conn.execute("""
            INSERT INTO albums(artist_id, title, label, genre, year, path, collection)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                artist_id  = excluded.artist_id,
                title      = excluded.title,
                label      = excluded.label,
                genre      = excluded.genre,
                year       = excluded.year,
                collection = excluded.collection
        """, (
            artist_id,
            meta["album"],
            meta["label"],
            meta["genre"],
            meta["year"],
            album_path,
            collection_num,
        ))

        seen_paths.add(album_path)
        print(f"  [C{collection_num}] {meta['artist']} — {meta['album']}")

    conn.commit()
    return seen_paths


def remove_stale(conn, collection_num, seen_paths):
    """Delete DB rows whose paths are no longer present on disk."""
    rows = conn.execute(
        "SELECT id, path FROM albums WHERE collection=?", (collection_num,)
    ).fetchall()
    removed = 0
    for row_id, path in rows:
        if path not in seen_paths:
            conn.execute("DELETE FROM albums WHERE id=?", (row_id,))
            # Prune orphaned artists
            conn.execute("""
                DELETE FROM artists
                WHERE id NOT IN (SELECT DISTINCT artist_id FROM albums)
            """)
            print(f"  Removed stale: {path}")
            removed += 1
    conn.commit()
    return removed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync music collections to DB")
    parser.add_argument(
        "--musicbrainz", action="store_true",
        help="Enrich metadata via MusicBrainz API (slow — 1 req/s)"
    )
    args = parser.parse_args()

    if args.musicbrainz:
        try:
            import musicbrainzngs
            musicbrainzngs.set_useragent("TrasvaseCollectionManager", "1.0", "admin@localhost")
        except ImportError:
            print("Warning: musicbrainzngs not installed. Skipping MusicBrainz lookup.")
            args.musicbrainz = False

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)

    print(f"Scanning Collection 1: {COLLECTION_1_PATH}")
    seen1 = scan_collection(conn, COLLECTION_1_PATH, 1, args.musicbrainz)

    print(f"\nScanning Collection 2: {COLLECTION_2_PATH}")
    seen2 = scan_collection(conn, COLLECTION_2_PATH, 2, args.musicbrainz)

    print("\nRemoving stale entries...")
    r1 = remove_stale(conn, 1, seen1)
    r2 = remove_stale(conn, 2, seen2)

    c1 = conn.execute("SELECT COUNT(*) FROM albums WHERE collection=1").fetchone()[0]
    c2 = conn.execute("SELECT COUNT(*) FROM albums WHERE collection=2").fetchone()[0]
    missing = conn.execute("""
        SELECT COUNT(*)
        FROM albums a2
        JOIN artists ar ON a2.artist_id = ar.id
        WHERE a2.collection = 2
          AND NOT EXISTS (
              SELECT 1 FROM albums a1
              JOIN artists ar1 ON a1.artist_id = ar1.id
              WHERE a1.collection = 1
                AND LOWER(ar1.name) = LOWER(ar.name)
                AND LOWER(a1.title) = LOWER(a2.title)
          )
    """).fetchone()[0]

    print(f"\n{'─'*50}")
    print(f"  Collection 1 : {c1} albums")
    print(f"  Collection 2 : {c2} albums")
    print(f"  Missing in C1: {missing} albums")
    print(f"  Stale removed: {r1 + r2} entries")
    print(f"{'─'*50}")

    conn.close()


if __name__ == "__main__":
    main()
