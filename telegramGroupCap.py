import asyncio
import os
import re
import json

from telethon import TelegramClient, events

from dotenv import load_dotenv

load_dotenv()

API_ID = os.getenv("API_ID")  
API_HASH = os.getenv("API_HASH")

SOURCE_CHAT = ""        # where you manually dump/upload videos
TARGET_CHAT = ""    # where captioned videos should be sent

MODE = "batch"                   # "batch" = process existing videos once and exit
                                  # "live"  = keep running, auto-forward new videos as they arrive

SENT_LOG = "forwarded_ids.json"  # tracks message IDs already forwarded, avoids duplicates
DELAY_SECONDS = 2                # small delay between forwards in batch mode

DELETE_AFTER_FORWARD = True   # add this near the top with your other config

# -------------------------------------------------------

client = TelegramClient("my_telegram_session", API_ID, API_HASH)


def filename_to_caption(filename: str) -> str:
    name = os.path.splitext(filename)[0]
    name = re.sub(r"[_\-]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def get_filename(message):
    if message.file and message.file.name:
        return message.file.name
    return f"video_{message.id}"


def load_sent_log():
    if os.path.exists(SENT_LOG):
        with open(SENT_LOG, "r") as f:
            return set(json.load(f))
    return set()


def save_sent_log(sent_set):
    with open(SENT_LOG, "w") as f:
        json.dump(list(sent_set), f)

async def forward_one(message, target, source, delete_after=False):
    filename = get_filename(message)
    caption = filename_to_caption(filename)
    await client.send_file(target, message.media, caption=caption)
    print(f"  Forwarded '{filename}' -> caption: '{caption}'")

    if delete_after:
        await client.delete_messages(source, message.id)
        print(f"  Deleted original from source chat.")

async def get_chat_by_name(client, name):
    if name.lstrip("-").isdigit():
        return int(name)
    async for dialog in client.iter_dialogs():
        if dialog.name == name:
            return dialog.entity
    available = [d.name for d in await client.get_dialogs() if d.is_group or d.is_channel]
    raise ValueError(f'No chat found with the name "{name}".\nAvailable groups/channels:\n' + "\n".join(available))



async def run_batch(source, target):
    sent_ids = load_sent_log()
    count = 0
    skipped_captioned = 0
    async for message in client.iter_messages(source, reverse=True):
        if message.id in sent_ids:
            continue
        if not (message.video or (message.file and message.file.mime_type and "video" in message.file.mime_type)):
            continue
        if message.text:   # already has a caption, don't touch it
            skipped_captioned += 1
            sent_ids.add(message.id)  # mark as handled so we don't re-check it every run
            continue

        try:
            await forward_one(message, target, source, delete_after=DELETE_AFTER_FORWARD)
            sent_ids.add(message.id)
            save_sent_log(sent_ids)
            count += 1
            await asyncio.sleep(DELAY_SECONDS)
        except Exception as e:
            print(f"  FAILED on message {message.id}: {e}")

    print(f"Batch complete. Forwarded {count} video(s).")


async def run_live(source, target):
    print(f"Listening for new videos in '{SOURCE_CHAT}'... (Ctrl+C to stop)")

    @client.on(events.NewMessage(chats=source))
    async def handler(event):
        message = event.message
        if not (message.video or (message.file and message.file.mime_type and "video" in message.file.mime_type)):
            return
        if message.text:  # already has a caption, skip
            return
        try:
            await forward_one(message, target, source, delete_after=DELETE_AFTER_FORWARD)
        except Exception as e:
            print(f"  FAILED on message {message.id}: {e}")

    await client.run_until_disconnected()


async def main():
    await client.start()
    print("Logged in successfully.")

    source = await get_chat_by_name(client, SOURCE_CHAT)
    target = await get_chat_by_name(client, TARGET_CHAT)

    if MODE == "batch":
        await run_batch(source, target)
    elif MODE == "live":
        await run_live(source, target)
    else:
        print('MODE must be "batch" or "live"')


if __name__ == "__main__":
    client.loop.run_until_complete(main())