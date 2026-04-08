# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Music collection sync tool. Two folders on a server:
- **Collection 1** — target/primary collection
- **Collection 2** — source to sync from

The web UI shows albums in Collection 2 that are **absent** from Collection 1, allows selecting them and copying them over. Albums can also be deleted from either collection.

## Setup

```bash
pip install -r requirements.txt

# Configure collection paths (edit config.py or use env vars)
export COLLECTION_1_PATH=/mnt/music/coleccion1
export COLLECTION_2_PATH=/mnt/music/coleccion2

python scan.py                # scan & sync DB
python scan.py --musicbrainz  # also enrich via MusicBrainz (slow, 1 req/s)

python app.py                 # start web server (default: 0.0.0.0:5000)
```

## File Structure

- **`config.py`** — paths and Flask settings (env-var overrideable)
- **`scan.py`** — scans both collection folders, syncs `music.db`. Handles initial creation and incremental updates (add new, remove stale).
- **`app.py`** — Flask app: serves `templates/index.html` + REST API
- **`templates/index.html`** — Single-page frontend, black/electric-green theme

## Database Schema (`music.db`, SQLite)

```sql
artists(id, name UNIQUE COLLATE NOCASE, mbid)
albums(id, artist_id FK, title, label, genre, year, path UNIQUE, collection 1|2, mbid)
```

One artist row per unique name; multiple albums share the same `artist_id`.

## API Routes

| Method   | Path                   | Description                                     |
|----------|------------------------|-------------------------------------------------|
| `GET`    | `/api/stats`           | Count totals for both collections + missing     |
| `GET`    | `/api/albums/missing`  | Albums in C2 with no matching artist+title in C1|
| `POST`   | `/api/albums/copy`     | Body: `{"ids": [...]}` — copy C2→C1             |
| `DELETE` | `/api/albums/<id>`     | Remove album from disk and DB                   |

## Metadata Priority

1. **mutagen** reads embedded FLAC tags (`ALBUMARTIST`, `ARTIST`, `ALBUM`, `DATE`, `GENRE`, `ORGANIZATION`/`LABEL`)
2. **Folder name** fallback when tags are missing (`Artist/Album` structure assumed)
3. **MusicBrainz** optional enrichment via `--musicbrainz` flag in `scan.py`

## "Missing" album logic

An album in C2 is considered missing from C1 if no row in C1 has the same `LOWER(artist.name)` AND `LOWER(album.title)`. Path is irrelevant — metadata identity is used.
