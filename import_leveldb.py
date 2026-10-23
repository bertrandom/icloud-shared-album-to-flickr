"""Import the old Node app's LevelDB cache into photos.db.

LevelDB has no pure-Python reader, so dump it with Node first:

    cd scripts && npm install && node dump_leveldb.mjs /path/to/photosdb > ../leveldb.json
    uv run import_leveldb.py leveldb.json
"""

import json
import sys

from main import open_db


def main():
    with open(sys.argv[1]) as f:
        entries = json.load(f)

    db = open_db()
    # The Node app logged album-add failures but never retried them, so treat
    # existing uploads as already in the album.
    before = db.total_changes
    db.executemany(
        "INSERT OR IGNORE INTO uploads (photo_guid, flickr_photo_id, added_to_album) VALUES (?, ?, 1)",
        entries,
    )
    db.commit()
    print(f"Imported {db.total_changes - before} of {len(entries)} entries")


if __name__ == "__main__":
    main()
