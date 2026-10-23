"""Sync photos from an iCloud Shared Album to Flickr."""

import argparse
import fcntl
import logging
import logging.handlers
import os
import sqlite3
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests
from dynaconf import Dynaconf
from oauthlib.oauth1 import Client as OAuth1Client
from requests_oauthlib import OAuth1

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "photos.db"
LOG_DIR = ROOT / "logs"
LOCK_PATH = ROOT / ".sync.lock"

URL_CHUNK_SIZE = 25
URL_CHUNK_DELAY = 1.0

# iCloud takes ~50s to answer webstream, so leave plenty of headroom.
ICLOUD_TIMEOUT = 180
ICLOUD_ATTEMPTS = 2

FLICKR_UPLOAD_URL = "https://up.flickr.com/services/upload/"
FLICKR_REST_URL = "https://api.flickr.com/services/rest"

BASE_62_CHAR_SET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

ICLOUD_HEADERS = {
    "Origin": "https://www.icloud.com",
    "Accept-Language": "en-US,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_4) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/56.0.2924.87 Safari/537.36"
    ),
    "Content-Type": "text/plain",
    "Accept": "*/*",
    "Referer": "https://www.icloud.com/sharedalbum/",
}

log = logging.getLogger("icloud-flickr")


def setup_logging(verbose):
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "sync.log", maxBytes=5 * 1024 * 1024, backupCount=5
    )
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)

    # Only echo to the console when run interactively, so cron doesn't need to
    # redirect stdout into an ever-growing file.
    if verbose or sys.stderr.isatty():
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        log.addHandler(console)

    log.setLevel(logging.DEBUG if verbose else logging.INFO)


def load_config(env=None):
    # settings.json has a section per environment ("local", "prod"); pick one
    # with --env or ICLOUD_FLICKR_ENV.
    return Dynaconf(
        root_path=ROOT / "config",
        settings_files=["settings.json"],
        environments=True,
        env=env or os.environ.get("ICLOUD_FLICKR_ENV", "local"),
        envvar_prefix="ICLOUD_FLICKR",
    )


def open_db(path=DB_PATH):
    db = sqlite3.connect(path)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS uploads (
            photo_guid TEXT PRIMARY KEY,
            flickr_photo_id TEXT NOT NULL,
            uploaded_at TEXT,
            added_to_album INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    return db


# --- iCloud ------------------------------------------------------------------


def base62_to_int(s):
    n = 0
    for c in s:
        n = n * 62 + BASE_62_CHAR_SET.index(c)
    return n


def get_base_url(token):
    partition = base62_to_int(token[1]) if token[0] == "A" else base62_to_int(token[1:3])
    return f"https://p{partition:02d}-sharedstreams.icloud.com/{token}/sharedstreams/"


def icloud_post(session, url, **kwargs):
    for attempt in range(1, ICLOUD_ATTEMPTS + 1):
        try:
            return session.post(url, timeout=ICLOUD_TIMEOUT, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as e:
            if attempt == ICLOUD_ATTEMPTS:
                raise
            log.warning("iCloud request to %s failed (%s), retrying", url, e)


def get_photo_metadata(session, base_url, token):
    """Return (base_url, photos) where photos maps photoGuid -> photo dict."""
    resp = icloud_post(session, base_url + "webstream", data='{"streamCtag":null}')

    # iCloud answers 330 with the host that actually serves this album.
    if resp.status_code == 330:
        host = resp.json()["X-Apple-MMe-Host"]
        base_url = f"https://{host}/{token}/sharedstreams/"
        resp = icloud_post(session, base_url + "webstream", data='{"streamCtag":null}')

    resp.raise_for_status()
    photos = {p["photoGuid"]: p for p in resp.json()["photos"]}
    return base_url, photos


def get_urls(session, base_url, photo_guids):
    resp = icloud_post(session, base_url + "webasseturls", json={"photoGuids": photo_guids})
    resp.raise_for_status()
    return {
        checksum: f"https://{item['url_location']}{item['url_path']}"
        for checksum, item in resp.json()["items"].items()
    }


def best_derivative(photo):
    derivatives = photo.get("derivatives", {}).values()
    return max(derivatives, key=lambda d: int(d.get("fileSize", 0)), default=None)


# --- Flickr ------------------------------------------------------------------


class Flickr:
    def __init__(self, config):
        self.config = config
        self.auth = OAuth1(
            config["api_key"],
            config["api_secret"],
            config["access_token"],
            config["access_token_secret"],
        )

    def upload(self, fileobj, filename, title):
        params = {
            "title": title,
            "is_public": "0",
            "is_friend": "1",
            "is_family": "1",
        }
        # Flickr wants every non-photo field in the OAuth signature, but the
        # request is multipart, so sign as if it were form-encoded.
        client = OAuth1Client(
            self.config["api_key"],
            client_secret=self.config["api_secret"],
            resource_owner_key=self.config["access_token"],
            resource_owner_secret=self.config["access_token_secret"],
        )
        _, headers, _ = client.sign(
            FLICKR_UPLOAD_URL,
            http_method="POST",
            body=urlencode(params),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = requests.post(
            FLICKR_UPLOAD_URL,
            data=params,
            files={"photo": (filename, fileobj)},
            headers={"Authorization": headers["Authorization"]},
            timeout=600,
        )
        resp.raise_for_status()
        rsp = ET.fromstring(resp.content)
        if rsp.get("stat") != "ok":
            err = rsp.find("err")
            raise RuntimeError(f"Flickr upload failed: {err.get('msg') if err is not None else resp.text}")
        return rsp.findtext("photoid")

    def call(self, method, **params):
        resp = requests.post(
            FLICKR_REST_URL,
            data={"method": method, "format": "json", "nojsoncallback": "1", **params},
            auth=self.auth,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("stat") != "ok":
            raise RuntimeError(f"{method} failed: {data.get('code')} {data.get('message')}")
        return data

    def add_to_album(self, album_id, photo_id):
        try:
            self.call("flickr.photosets.addPhoto", photoset_id=album_id, photo_id=photo_id)
        except RuntimeError as e:
            # Code 3: photo already in set.
            if "failed: 3 " not in str(e):
                raise


# --- Sync --------------------------------------------------------------------


def upload_photo(flickr, photo, url):
    filename = os.path.basename(urlparse(url).path) or f"{photo['photoGuid']}.jpg"
    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with tempfile.SpooledTemporaryFile(max_size=50 * 1024 * 1024) as tmp:
            for chunk in resp.iter_content(1024 * 1024):
                tmp.write(chunk)
            tmp.seek(0)
            return flickr.upload(tmp, filename, photo.get("caption") or "")


def sync(config, db, dry_run=False):
    token = config["icloud_album_token"]
    album_id = config.get("flickr_album_id")

    session = requests.Session()
    session.headers.update(ICLOUD_HEADERS)

    base_url, photos = get_photo_metadata(session, get_base_url(token), token)

    # Only look up download URLs for photos we haven't uploaded yet; the album
    # metadata alone is enough to know what's new.
    uploaded = {row[0] for row in db.execute("SELECT photo_guid FROM uploads")}
    new_guids = [guid for guid in photos if guid not in uploaded]
    log.info("Album has %d photos, %d already uploaded, %d new",
             len(photos), len(photos) - len(new_guids), len(new_guids))

    flickr = Flickr(config)

    if new_guids:
        urls = {}
        for i in range(0, len(new_guids), URL_CHUNK_SIZE):
            if i:
                time.sleep(URL_CHUNK_DELAY)
            urls.update(get_urls(session, base_url, new_guids[i : i + URL_CHUNK_SIZE]))

        uploaded_count = failed = 0
        for guid in new_guids:
            photo = photos[guid]
            derivative = best_derivative(photo)
            url = derivative and urls.get(derivative["checksum"])
            if not url:
                log.warning("No download URL for %s, skipping", guid)
                failed += 1
                continue

            if dry_run:
                log.info("Would upload %s (%s bytes)", guid, derivative.get("fileSize"))
                continue

            try:
                photo_id = upload_photo(flickr, photo, url)
            except Exception as e:
                log.error("Failed to upload %s: %s", guid, e)
                failed += 1
                continue

            db.execute(
                "INSERT INTO uploads (photo_guid, flickr_photo_id, uploaded_at) VALUES (?, ?, ?)",
                (guid, photo_id, datetime.now(timezone.utc).isoformat()),
            )
            db.commit()
            uploaded_count += 1
            log.info("Uploaded %s as %s", guid, photo_id)

        log.info("Uploaded %d, failed %d", uploaded_count, failed)

    if album_id and not dry_run:
        add_pending_to_album(flickr, db, album_id)


def add_pending_to_album(flickr, db, album_id):
    pending = db.execute(
        "SELECT photo_guid, flickr_photo_id FROM uploads WHERE added_to_album = 0"
    ).fetchall()
    for guid, photo_id in pending:
        try:
            flickr.add_to_album(album_id, photo_id)
        except Exception as e:
            log.error("Failed to add %s to album %s: %s", photo_id, album_id, e)
            continue
        db.execute("UPDATE uploads SET added_to_album = 1 WHERE photo_guid = ?", (guid,))
        db.commit()
        log.info("Added %s to album %s", photo_id, album_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="find new photos but don't upload")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging, echo to console")
    parser.add_argument("--env", help="config environment (default: $ICLOUD_FLICKR_ENV or local)")
    args = parser.parse_args()

    setup_logging(args.verbose)

    # Keep overlapping cron runs from uploading the same photo twice.
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.info("Another sync is already running, exiting")
        return

    try:
        sync(load_config(args.env), open_db(), dry_run=args.dry_run)
    except Exception:
        log.exception("Sync failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
