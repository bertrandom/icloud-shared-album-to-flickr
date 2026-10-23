# icloud-shared-album-to-flickr

Syncs photos from an iCloud Shared Album to Flickr. Python rewrite of
[icloud-shared-album-to-flickr-nodejs](../icloud-shared-album-to-flickr-nodejs).

Apple has no API for iCloud Shared Albums, so this mimics the requests the
public album website makes. For each photo it uploads the largest derivative to
Flickr (visible to friends and family) and adds it to an album.

Uploaded photos are recorded in `photos.db` (SQLite). Each run fetches the album
listing once and only requests download URLs for photos not already in the
database.

## Setup

```
uv sync
cp config/settings.example.json config/settings.json   # fill in Flickr creds, album token, album id
```

Config is loaded with [Dynaconf](https://www.dynaconf.com/) from `config/settings.json`,
which has a `local` and a `prod` section. `local` is used unless `--env prod` or
`ICLOUD_FLICKR_ENV=prod` is given. Any key can also be overridden from the
environment with an `ICLOUD_FLICKR_` prefix (e.g. `ICLOUD_FLICKR_FLICKR_ALBUM_ID`).

If your public album URL is `https://www.icloud.com/sharedalbum/#B0z5qAGN1JIFd3y`,
the iCloud Album Token is `B0z5qAGN1JIFd3y`.

## Usage

```
uv run main.py            # sync
uv run main.py --dry-run  # list what would be uploaded
uv run main.py -v         # debug logging, echoed to the console
uv run main.py --env prod # use the prod config
```

Logs go to `logs/sync.log`, rotated at 5 MB with 5 backups. When run without a
terminal (e.g. cron) nothing is written to stdout, so there's no need to
redirect output:

```
0 * * * * cd /web/icloud-shared-album-to-flickr && ~/.local/bin/uv run main.py --env prod
```

A lock file prevents overlapping runs.

## Migrating from the Node version

```
cd scripts && npm install && node dump_leveldb.mjs /path/to/photosdb > ../leveldb.json && cd ..
uv run import_leveldb.py leveldb.json
```

The import is idempotent. LevelDB only stored photo IDs, so then fill in upload
dates from Flickr:

```
uv run backfill_uploaded_at.py
```

## Deploying

```
./deploy.sh -n   # dry run
./deploy.sh
```

Copies the code and `config/settings.json` to `server:/web/icloud-shared-album-to-flickr/`
and runs `uv sync` there. `photos.db` and `logs/` on the server are left alone.
