#!/usr/bin/env python3
"""
Flask app — music collection manager.
Exposes REST API and serves the frontend.
"""

import os
import shutil
import sqlite3

from flask import Flask, jsonify, request, render_template, abort
from config import COLLECTION_1_PATH, COLLECTION_2_PATH, DB_PATH, FLASK_HOST, FLASK_PORT

app = Flask(__name__)


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


MISSING_QUERY = """
    SELECT a2.id, ar.name AS artist, a2.title, a2.label, a2.genre, a2.year, a2.path
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
    ORDER BY ar.name COLLATE NOCASE, a2.year DESC, a2.title COLLATE NOCASE
"""


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API — read
# ---------------------------------------------------------------------------

@app.route("/api/stats")
def api_stats():
    conn = get_db()
    stats = {
        "collection1": conn.execute(
            "SELECT COUNT(*) FROM albums WHERE collection=1"
        ).fetchone()[0],
        "collection2": conn.execute(
            "SELECT COUNT(*) FROM albums WHERE collection=2"
        ).fetchone()[0],
        "artists": conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0],
        "missing":  conn.execute(
            f"SELECT COUNT(*) FROM ({MISSING_QUERY})"
        ).fetchone()[0],
    }
    conn.close()
    return jsonify(stats)


@app.route("/api/albums/missing")
def api_missing():
    conn = get_db()
    rows = conn.execute(MISSING_QUERY).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# API — copy
# ---------------------------------------------------------------------------

@app.route("/api/albums/copy", methods=["POST"])
def api_copy():
    data = request.get_json(silent=True) or {}
    album_ids = data.get("ids", [])
    if not album_ids:
        return jsonify({"error": "No album IDs provided"}), 400

    conn = get_db()
    results = []

    for album_id in album_ids:
        row = conn.execute("""
            SELECT a.id, a.path, a.title, a.artist_id, ar.name AS artist,
                   a.label, a.genre, a.year
            FROM albums a
            JOIN artists ar ON a.artist_id = ar.id
            WHERE a.id = ? AND a.collection = 2
        """, (album_id,)).fetchone()

        if not row:
            results.append({"id": album_id, "success": False, "error": "Album not found"})
            continue

        src_path = row["path"]
        # Preserve relative folder structure inside the collection
        rel      = os.path.relpath(src_path, COLLECTION_2_PATH)
        dst_path = os.path.join(COLLECTION_1_PATH, rel)

        if os.path.exists(dst_path):
            results.append({"id": album_id, "success": False, "error": "Destination already exists"})
            continue

        try:
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            shutil.copytree(src_path, dst_path)

            # Upsert artist then album in collection 1
            conn.execute(
                "INSERT INTO artists(name) VALUES(?) ON CONFLICT(name) DO NOTHING",
                (row["artist"],)
            )
            artist_id = conn.execute(
                "SELECT id FROM artists WHERE name=? COLLATE NOCASE", (row["artist"],)
            ).fetchone()[0]

            conn.execute("""
                INSERT INTO albums(artist_id, title, label, genre, year, path, collection)
                VALUES(?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(path) DO NOTHING
            """, (artist_id, row["title"], row["label"], row["genre"], row["year"], dst_path))

            conn.commit()
            results.append({"id": album_id, "success": True, "dest": dst_path})

        except Exception as exc:
            results.append({"id": album_id, "success": False, "error": str(exc)})

    conn.close()
    return jsonify(results)


# ---------------------------------------------------------------------------
# API — delete
# ---------------------------------------------------------------------------

@app.route("/api/albums/<int:album_id>", methods=["DELETE"])
def api_delete(album_id):
    conn = get_db()
    row = conn.execute(
        "SELECT id, path, artist_id FROM albums WHERE id=?", (album_id,)
    ).fetchone()

    if not row:
        conn.close()
        return jsonify({"error": "Album not found"}), 404

    try:
        if os.path.exists(row["path"]):
            shutil.rmtree(row["path"])

        conn.execute("DELETE FROM albums WHERE id=?", (album_id,))
        conn.execute("""
            DELETE FROM artists
            WHERE id=? AND id NOT IN (SELECT DISTINCT artist_id FROM albums)
        """, (row["artist_id"],))
        conn.commit()
        conn.close()
        return jsonify({"success": True})

    except Exception as exc:
        conn.close()
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=True)
