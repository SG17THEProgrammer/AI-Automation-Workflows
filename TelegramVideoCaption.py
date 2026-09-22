"""
Telegram Video Auto-Uploader
----------------------------
Uploads every video file in a folder to a Telegram group, using the
filename (cleaned up) as the caption automatically.

SETUP (one-time):
1. Install dependency:
      pip install telethon

2. Get your free API credentials:
   - Go to https://my.telegram.org -> Log in with your phone number
   - Click "API development tools"
   - Create an app (any name/description works)
   - Copy the "api_id" and "api_hash" values

3. Fill in API_ID, API_HASH, and GROUP_NAME below.

4. Run the script:
      python telegram_video_uploader.py

   The first time you run it, it'll ask for your phone number and the
   login code Telegram sends you (this logs in as YOUR account, not a
   bot). After that, it saves a session file so you won't need to log
   in again.

5. Put your videos in the FOLDER_PATH below and run it whenever you
   want to bulk-upload. Already-sent files are tracked so re-running
   won't duplicate them.
"""

import asyncio
import os
import re
import json

from telethon import TelegramClient
from dotenv import load_dotenv

load_dotenv()

API_ID = os.getenv("API_ID")  
API_HASH = os.getenv("API_HASH")

GROUP_NAME = os.getenv("GROUP_NAME")         # <-- exact name of your Telegram group
FOLDER_PATH = os.getenv("FOLDER_PATH")   # <-- folder with videos
DELAY_SECONDS = 5                    # wait between uploads (avoids rate limits)
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".webm")
SENT_LOG = "sent_files.json"         # tracks what's already been uploaded
# -------------------------------------------------------

client = TelegramClient("my_telegram_session", API_ID, API_HASH)


def filename_to_caption(filename: str) -> str:
    """Turn 'my_cool_trip-part_2.mp4' into 'my cool trip part 2'."""
    name = os.path.splitext(filename)[0]
    name = re.sub(r"[_\-]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def load_sent_log():
    if os.path.exists(SENT_LOG):
        with open(SENT_LOG, "r") as f:
            return set(json.load(f))
    return set()


def save_sent_log(sent_set):
    with open(SENT_LOG, "w") as f:
        json.dump(list(sent_set), f)

async def get_group_by_name(client, name):
    async for dialog in client.iter_dialogs():
        if dialog.name == name:
            return dialog.entity
    raise ValueError(f'No dialog found with the name "{name}". Available groups/chats:\n' +
                      "\n".join(d.name for d in await client.get_dialogs() if d.is_group or d.is_channel))


async def main():
    await client.start()
    print("Logged in successfully.")

    group = await get_group_by_name(client, GROUP_NAME)

    sent_files = load_sent_log()

    all_files = sorted(
        f for f in os.listdir(FOLDER_PATH)
        if f.lower().endswith(VIDEO_EXTENSIONS)
    )
    pending = [f for f in all_files if f not in sent_files]

    if not pending:
        print("No new videos to upload.")
        return

    print(f"Found {len(pending)} new video(s) to upload.")

    for i, filename in enumerate(pending, start=1):
        filepath = os.path.join(FOLDER_PATH, filename)
        caption = filename_to_caption(filename)

        print(f"[{i}/{len(pending)}] Uploading: {filename}  -> caption: '{caption}'")

        try:
            await client.send_file(
                group,
                filepath,
                caption=caption,
                supports_streaming=True,
            )
            sent_files.add(filename)
            save_sent_log(sent_files)  # save progress after each success
            print("   Done.")
        except Exception as e:
            print(f"   FAILED: {e}")
            # keep going with the next file instead of stopping the whole batch

        if i < len(pending):
            await asyncio.sleep(DELAY_SECONDS)

    print("All done.")


if __name__ == "__main__":
    with client:
        client.loop.run_until_complete(main())