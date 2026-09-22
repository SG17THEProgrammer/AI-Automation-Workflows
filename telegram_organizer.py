"""
Telegram Group Organizer
========================
- Scans TARGET_FOLDERS for groups
- Routes each group into a supergroup named just the letter (e.g. "A", "B", "Others")
- Forwards ALL messages (everything: text, media, files) from the source group into
  the matching topic inside the letter supergroup
- No duplicates ever: progress is saved to a JSON file and checked on every run
- After all messages are forwarded, the supergroup is archived (folder_id=1)
- Monitors continuously every POLL_INTERVAL seconds for newly joined groups
"""

import asyncio
import json
import logging
import os
import random
import string
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.functions.channels import CreateChannelRequest, EditTitleRequest
from telethon.tl.functions.folders import EditPeerFoldersRequest
from telethon.tl.functions.messages import (
    CreateForumTopicRequest,
    ForwardMessagesRequest,
    GetDialogFiltersRequest,
    GetForumTopicsRequest,
)
from telethon.tl.types import (
    Channel,
    Chat,
    DialogFilterDefault,
    InputFolderPeer,
    InputPeerChannel,
    InputReplyToMessage,
)
from telethon.errors import FloodWaitError
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────────────────────

API_ID = os.getenv("API_ID")  
API_HASH = os.getenv("API_HASH")
SESSION_NAME = os.getenv("SESSION_NAME")

# Get the raw string
raw_folders = os.getenv("TARGET_FOLDERS", "")

# Folders to scan (case-insensitive). Add more names in env when needed.
TARGET_FOLDERS = raw_folders.split(",")

# Seconds between re-scans for new groups
POLL_INTERVAL = 120

# How many messages to forward in one batch (Telegram max = 100)
FORWARD_BATCH = 100

# Seconds to wait between batches to stay under rate limits
BATCH_DELAY = 2

# File that tracks which groups have been fully forwarded
PROGRESS_FILE = "organizer_progress.json"

# ──────────────────────────────────────────────────────────────
# LOGGING  (UTF-8 forced so emoji/arrows don't crash Windows)
# ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(
            stream=open(1, mode="w", encoding="utf-8", closefd=False)
        ),
        logging.FileHandler("telegram_organizer.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────

def bucket_for(name: str) -> str:
    stripped = name.strip()
    if not stripped:
        return "Others"
    first = stripped[0].upper()
    return first if first in string.ascii_uppercase else "Others"


def supergroup_title(bucket: str) -> str:
    # Just the letter/word — clean and simple
    return bucket  # "A", "B", … "Z", "Others"


# ──────────────────────────────────────────────────────────────
# PROGRESS TRACKER  (persists across restarts — no duplicates)
# ──────────────────────────────────────────────────────────────

class Progress:
    """
    Tracks state in a JSON file so restarts are safe.

    Structure:
    {
      "<group_id>": {
        "name": "Anna",
        "topic_id": 123,
        "supergroup_id": 456,
        "last_forwarded_msg_id": 789,  # 0 = none yet, -1 = fully done
        "archived": true/false
      }
    }
    """

    def __init__(self, path: str = PROGRESS_FILE):
        self.path = Path(path)
        self._data: dict = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
                log.info("Loaded progress for %d groups from %s", len(self._data), self.path)
            except Exception as e:
                log.warning("Could not read progress file: %s — starting fresh", e)
                self._data = {}

    def _save(self):
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    def get(self, group_id: int) -> dict | None:
        return self._data.get(str(group_id))

    def set(self, group_id: int, **kwargs):
        key = str(group_id)
        if key not in self._data:
            self._data[key] = {}
        self._data[key].update(kwargs)
        self._save()

    def is_fully_done(self, group_id: int) -> bool:
        entry = self.get(group_id)
        return entry is not None and entry.get("last_forwarded_msg_id") == -1

    def is_archived(self, group_id: int) -> bool:
        entry = self.get(group_id)
        return entry is not None and entry.get("archived", False)

    def last_forwarded(self, group_id: int) -> int:
        entry = self.get(group_id)
        return entry.get("last_forwarded_msg_id", 0) if entry else 0


# ──────────────────────────────────────────────────────────────
# MAIN ORGANISER
# ──────────────────────────────────────────────────────────────

class TelegramOrganizer:
    def __init__(self, client: TelegramClient):
        self.client = client
        self.progress = Progress()
        # in-memory caches (rebuilt each run from Telegram + progress file)
        self._supergroup_cache: dict[str, Channel] = {}
        self._topic_cache: dict[tuple[int, str], int] = {}

    # ── folder scanning ───────────────────────────────────────

    async def _get_folder_peer_ids(self) -> set[int]:
        result = await self.client(GetDialogFiltersRequest())
        peer_ids: set[int] = set()
        for f in result.filters:
            if isinstance(f, DialogFilterDefault):
                continue
            title = getattr(f, "title", None)
            if title is None:
                continue
            title_str = title if isinstance(title, str) else getattr(title, "text", "")
            if title_str.lower() not in [t.lower() for t in TARGET_FOLDERS]:
                continue
            for peer in getattr(f, "include_peers", []):
                cid = getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None)
                if cid:
                    peer_ids.add(cid)
        log.info("Found %d peers in target folders %s", len(peer_ids), TARGET_FOLDERS)
        return peer_ids

    async def _get_groups_in_folders(self) -> list:
        folder_ids = await self._get_folder_peer_ids()
        if not folder_ids:
            log.warning("No peers found. Check TARGET_FOLDERS names match exactly.")
            return []

        log.info("Folder peer IDs to match: %s", folder_ids)

        groups = []
        seen = 0
        # limit=None ensures ALL dialogs are fetched, not just the first ~100
        async for dialog in self.client.iter_dialogs(limit=None):
            entity = dialog.entity
            seen += 1
            if not isinstance(entity, (Channel, Chat)):
                continue
            eid = getattr(entity, "id", None)
            if eid in folder_ids:
                groups.append(entity)

        log.info("Scanned %d total dialogs, matched %d groups from target folders",
                 seen, len(groups))

        # Log every matched group so you can verify nothing is missing
        for g in groups:
            log.info("  -> matched: [%d] '%s'", g.id, getattr(g, "title", "?"))

        return groups

    # ── rename old supergroups ────────────────────────────────

    async def rename_old_supergroups(self):
        """
        One-time migration: find any supergroup named "Organizer - X"
        and rename it to just "X". Runs at startup automatically.
        """
        import re
        old_pattern = re.compile(r"^Organizer\s*-\s*(.+)$", re.IGNORECASE)
        renamed = 0
        async for dialog in self.client.iter_dialogs(limit=None):
            entity = dialog.entity
            if not (isinstance(entity, Channel) and entity.megagroup and entity.forum):
                continue
            match = old_pattern.match(dialog.title or "")
            if not match:
                continue
            new_title = match.group(1).strip()  # "A", "B", "Others" etc.
            log.info("Renaming '%s' -> '%s'", dialog.title, new_title)
            try:
                await self.client(EditTitleRequest(channel=entity, title=new_title))
                renamed += 1
                await asyncio.sleep(1)
            except Exception as e:
                log.error("Could not rename '%s': %s", dialog.title, e)
        if renamed:
            log.info("Renamed %d old supergroups.", renamed)
        else:
            log.info("No old-style supergroups found to rename.")

    # ── supergroup helpers ────────────────────────────────────

    async def _get_or_create_supergroup(self, bucket: str) -> Channel:
        if bucket in self._supergroup_cache:
            return self._supergroup_cache[bucket]

        title = supergroup_title(bucket)
        async for dialog in self.client.iter_dialogs(limit=None):
            entity = dialog.entity
            if (
                isinstance(entity, Channel)
                and entity.megagroup
                and entity.forum
                and dialog.title == title
            ):
                log.info("Found existing supergroup: '%s'", title)
                self._supergroup_cache[bucket] = entity
                return entity

        log.info("Creating supergroup: '%s'", title)
        result = await self.client(
            CreateChannelRequest(
                title=title,
                about=f"Groups starting with '{bucket}'",
                megagroup=True,
                forum=True,
            )
        )
        entity = result.chats[0]
        self._supergroup_cache[bucket] = entity
        await asyncio.sleep(1)
        return entity

    async def _get_existing_topics(self, supergroup: Channel) -> dict[str, int]:
        result = await self.client(
            GetForumTopicsRequest(
                peer=supergroup,
                offset_date=None,
                offset_id=0,
                offset_topic=0,
                limit=100,
            )
        )
        topics = {}
        for topic in result.topics:
            topics[topic.title] = topic.id
            self._topic_cache[(supergroup.id, topic.title)] = topic.id
        return topics

    async def _get_or_create_topic(self, supergroup: Channel, topic_title: str) -> int:
        cache_key = (supergroup.id, topic_title)
        if cache_key in self._topic_cache:
            return self._topic_cache[cache_key]

        existing = await self._get_existing_topics(supergroup)
        if topic_title in existing:
            return existing[topic_title]

        log.info("Creating topic '%s' in '%s'", topic_title, supergroup.title)
        result = await self.client(
            CreateForumTopicRequest(peer=supergroup, title=topic_title)
        )
        topic_id = None
        for update in result.updates:
            tid = getattr(update, "id", None)
            if tid:
                topic_id = tid
                break
        if topic_id is None:
            raise RuntimeError(f"Could not get topic_id for '{topic_title}'")
        self._topic_cache[cache_key] = topic_id
        await asyncio.sleep(1)
        return topic_id

    # ── archive helper ────────────────────────────────────────

    async def _archive_supergroup(self, supergroup: Channel):
        """Move a supergroup to the Archive (folder_id = 1)."""
        try:
            input_peer = await self.client.get_input_entity(supergroup)
            await self.client(
                EditPeerFoldersRequest(
                    folder_peers=[InputFolderPeer(peer=input_peer, folder_id=1)]
                )
            )
            log.info("Archived supergroup '%s'", supergroup.title)
        except Exception as e:
            log.warning("Could not archive '%s': %s", supergroup.title, e)

    # ── message forwarding ────────────────────────────────────

    async def _forward_all_messages(
        self,
        source_group,
        supergroup: Channel,
        topic_id: int,
        group_id: int,
    ) -> bool:
        """
        Forward every message from source_group into the topic.
        Returns True if fully done (including groups with 0 messages).
        Resumes from the last forwarded message id stored in progress.
        """
        last_id = self.progress.last_forwarded(group_id)
        name = getattr(source_group, "title", str(group_id))

        log.info("Forwarding from '%s' (after msg_id=%d)...", name, last_id)

        # Collect all message IDs oldest -> newest, skipping already forwarded
        all_ids = []
        try:
            async for msg in self.client.iter_messages(source_group, reverse=True):
                if msg.id <= last_id:
                    continue
                all_ids.append(msg.id)
        except Exception as e:
            log.error("Cannot read messages from '%s': %s", name, e)
            return False

        if not all_ids:
            log.info("'%s': nothing new to forward.", name)
            return True   # genuinely empty or all already forwarded

        log.info("'%s': %d messages to forward into topic #%d", name, len(all_ids), topic_id)

        total_forwarded = 0
        failed_batches = 0
        i = 0

        # Use a while loop so we can properly retry on FloodWait
        while i < len(all_ids):
            batch = all_ids[i : i + FORWARD_BATCH]
            success = False
            retries = 0

            while not success and retries < 3:
                try:
                    await self.client(
                        ForwardMessagesRequest(
                            from_peer=source_group,
                            id=batch,
                            to_peer=supergroup,
                            top_msg_id=topic_id,
                            drop_author=False,
                            silent=True,
                            random_id=[random.randint(0, 2**31) for _ in batch],
                        )
                    )
                    total_forwarded += len(batch)
                    # Only advance progress after confirmed success
                    self.progress.set(group_id, last_forwarded_msg_id=batch[-1])
                    log.info("  '%s': %d/%d forwarded", name, total_forwarded, len(all_ids))
                    await asyncio.sleep(BATCH_DELAY)
                    success = True

                except FloodWaitError as e:
                    log.warning("FloodWait %ds — sleeping...", e.seconds)
                    await asyncio.sleep(e.seconds + 5)
                    # don't increment retries, just wait and retry

                except Exception as e:
                    retries += 1
                    err_str = str(e)
                    if "restricted" in err_str.lower() or "forward" in err_str.lower() or "forbidden" in err_str.lower():
                        # Group has forwarding disabled — copy as text instead
                        log.warning("'%s' is forward-restricted. Copying as text...", name)
                        await self._copy_as_text(source_group, supergroup, topic_id, batch, name)
                        self.progress.set(group_id, last_forwarded_msg_id=batch[-1])
                        total_forwarded += len(batch)
                        success = True
                    else:
                        log.error("Batch error from '%s' (attempt %d/3): %s", name, retries, e)
                        await asyncio.sleep(3 * retries)

            if not success:
                log.error("Skipping batch after 3 failed attempts for '%s'", name)
                failed_batches += 1
                # Still advance so we don't get stuck on one bad batch forever
                self.progress.set(group_id, last_forwarded_msg_id=batch[-1])

            i += FORWARD_BATCH

        log.info("'%s': done. Forwarded=%d, failed_batches=%d", name, total_forwarded, failed_batches)
        return True

    async def _copy_as_text(self, source_group, supergroup, topic_id, msg_ids, name):
        """Fallback for forward-restricted groups: copy message text/caption as plain text."""
        for mid in msg_ids:
            try:
                msg = await self.client.get_messages(source_group, ids=mid)
                if not msg:
                    continue
                text = msg.text or msg.message or ""
                caption = getattr(msg, "caption", "") or ""
                content = text or caption
                if content:
                    await self.client.send_message(
                        entity=supergroup,
                        message=f"[From {name}] {content}",
                        reply_to=topic_id,
                    )
                    await asyncio.sleep(0.5)
            except Exception as e:
                log.debug("Could not copy msg %d from '%s': %s", mid, name, e)

    # ── main per-group logic ──────────────────────────────────

    async def organise_group(self, group):
        gid = group.id
        name = group.title or ""
        bucket = bucket_for(name)

        # ── 1. Get/create supergroup and topic ────────────────
        supergroup = await self._get_or_create_supergroup(bucket)
        topic_id   = await self._get_or_create_topic(supergroup, name)

        # Save supergroup/topic ids to progress immediately
        self.progress.set(
            gid,
            name=name,
            bucket=bucket,
            supergroup_id=supergroup.id,
            topic_id=topic_id,
        )

        # ── 2. Forward messages (skip if already fully done) ──
        if not self.progress.is_fully_done(gid):
            success = await self._forward_all_messages(group, supergroup, topic_id, gid)
            if success:
                # Only mark done when forwarding actually completed
                self.progress.set(gid, last_forwarded_msg_id=-1)
                log.info("[DONE] '%s' -> '%s' / topic #%d", name, supergroup.title, topic_id)
            else:
                log.warning("[INCOMPLETE] '%s' had errors — will retry next pass", name)
        else:
            log.info("[SKIP] '%s' already fully forwarded", name)

        # ── 3. Archive the supergroup once forwarding is done ─
        # (archive only after ALL groups in this bucket are done — checked below)

    async def organise_all(self):
        log.info("=== Organise pass started ===")
        groups = await self._get_groups_in_folders()

        # Group by bucket so we can archive after each bucket is fully done
        from collections import defaultdict
        by_bucket: dict[str, list] = defaultdict(list)
        for g in groups:
            by_bucket[bucket_for(g.title or "")].append(g)

        for bucket, bucket_groups in by_bucket.items():
            log.info("--- Bucket '%s': %d groups ---", bucket, len(bucket_groups))
            for group in bucket_groups:
                try:
                    await self.organise_group(group)
                except Exception as e:
                    log.error("Error on '%s': %s", getattr(group, "title", "?"), e)

            # Archive the supergroup if every group in this bucket is done
            all_done = all(self.progress.is_fully_done(g.id) for g in bucket_groups)
            if all_done:
                supergroup = self._supergroup_cache.get(bucket)
                if supergroup and not self.progress.is_archived(
                    supergroup.id
                ):
                    await self._archive_supergroup(supergroup)
                    self.progress.set(supergroup.id, archived=True)

        log.info("=== Organise pass complete ===")

    # ── continuous monitor ────────────────────────────────────

    async def monitor(self):
        # Rename any "Organizer - X" supergroups to "X" on first boot
        await self.rename_old_supergroups()
        while True:
            try:
                await self.organise_all()
            except Exception as e:
                log.error("Monitor error: %s", e)
            log.info("Next check in %ds...", POLL_INTERVAL)
            await asyncio.sleep(POLL_INTERVAL)


# ──────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────

async def main():
    if not API_ID or not API_HASH:
        raise ValueError(
            "Set API_ID and API_HASH at the top of this script.\n"
            "Get them from https://my.telegram.org -> 'API development tools'."
        )
    async with TelegramClient(SESSION_NAME, API_ID, API_HASH) as client:
        me = await client.get_me()
        log.info("Logged in as: %s", me.first_name)
        organizer = TelegramOrganizer(client)
        await organizer.monitor()


# ──────────────────────────────────────────────────────────────
# REPAIR TOOL  — run with: python telegram_organizer.py --repair
# Resets "done" status for groups whose topics appear empty so
# they get re-forwarded on the next run.
# ──────────────────────────────────────────────────────────────

async def repair():
    """
    Clears the 'fully done' flag for every group in the progress file
    that you suspect was marked done without content actually arriving.
    Pass specific group names as args, or reset ALL if none given.
    Usage:
        python telegram_organizer.py --repair              # reset ALL
        python telegram_organizer.py --repair "anna" "S"  # reset by name/bucket
    """
    import sys
    targets = [a.lower() for a in sys.argv[2:]]  # optional name filters
    p = Progress()
    reset_count = 0
    for gid, entry in p._data.items():
        name = entry.get("name", "")
        bucket = entry.get("bucket", "")
        if entry.get("last_forwarded_msg_id") == -1:
            if not targets or name.lower() in targets or bucket.lower() in targets:
                p._data[gid]["last_forwarded_msg_id"] = 0
                reset_count += 1
                print(f"Reset: [{gid}] '{name}' (bucket {bucket})")
    p._save()
    print(f"\nReset {reset_count} group(s). Run the script normally to re-forward them.")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--repair":
        asyncio.run(repair())
    else:
        asyncio.run(main())