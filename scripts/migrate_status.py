"""One-off migration: split the old "done" episode status into "tts_done" (translation
+ TTS finished, awaiting human review) and "reviewed" (every row human_verified).

Run once after deploying the tts_done/reviewed status split, from the project root:
    python scripts/migrate_status.py [--dry-run]

Safe to re-run: episodes already carrying "tts_done" or "reviewed" are left untouched,
and core.db.get_episode()/list_episodes_for_language() already normalize "done" on
read, so this script only matters for persisting the corrected status into Mongo
(e.g. so `db.status` queries/indexes reflect reality) rather than recomputing it
on every read.
"""
import argparse
import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv()

from core import db  # noqa: E402 -- must follow load_dotenv() so MONGO_URI is set


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing.")
    args = parser.parse_args()

    coll = db.episodes_collection()
    changed = 0
    for doc in coll.find({"status": {"$in": list(db.LEGACY_DONE_STATUSES)}}):
        episode_id = str(doc["_id"])
        counts = db._row_counts(episode_id)
        all_verified = counts["total_rows"] > 0 and counts["verified_rows"] == counts["total_rows"]
        new_status = db.STATUS_REVIEWED if all_verified else db.STATUS_TTS_DONE
        print(f"episode {episode_id} ({doc.get('title')!r}): "
              f"{counts['verified_rows']}/{counts['total_rows']} verified -> status {doc['status']!r} -> {new_status!r}")
        if not args.dry_run:
            db.set_episode_status(episode_id, new_status)
        changed += 1

    print(f"\n{'Would update' if args.dry_run else 'Updated'} {changed} episode(s).")


if __name__ == "__main__":
    main()
