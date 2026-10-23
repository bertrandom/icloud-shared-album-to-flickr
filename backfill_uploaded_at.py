"""Fill in uploaded_at for rows imported from LevelDB, using Flickr's upload dates."""

from datetime import datetime, timezone

from main import Flickr, load_config, open_db


def main():
    db = open_db()
    missing = {
        row[0]
        for row in db.execute("SELECT flickr_photo_id FROM uploads WHERE uploaded_at IS NULL")
    }
    print(f"{len(missing)} rows without uploaded_at")

    flickr = Flickr(load_config())
    dates = {}
    page = pages = 1
    while page <= pages:
        data = flickr.call(
            "flickr.people.getPhotos",
            user_id="me",
            extras="date_upload",
            per_page="500",
            page=str(page),
        )["photos"]
        pages = data["pages"]
        for photo in data["photo"]:
            if photo["id"] in missing:
                dates[photo["id"]] = datetime.fromtimestamp(
                    int(photo["dateupload"]), timezone.utc
                ).isoformat()
        page += 1

    db.executemany(
        "UPDATE uploads SET uploaded_at = ? WHERE flickr_photo_id = ? AND uploaded_at IS NULL",
        [(date, photo_id) for photo_id, date in dates.items()],
    )
    db.commit()
    print(f"Filled {len(dates)}; {len(missing) - len(dates)} not found on Flickr")


if __name__ == "__main__":
    main()
