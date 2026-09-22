"""
Telegram Global Live Auto-Captioner
-------------------------------------
Watches ALL your chats/groups. Whenever YOU upload a video anywhere
(without a caption), this automatically adds a caption to it (derived
from the video's filename) by editing that same message in place.

No forwarding, no re-upload, no need to pick a specific chat -
works no matter which of your groups/chats you upload into.
Telegram's own servers handle the actual video hosting; this script
only edits the caption text.

Meant to run continuously on a server (not your laptop), so it's
always listening even when your PC is off.
"""

import os
import re
from telethon import TelegramClient, events
from dotenv import load_dotenv

load_dotenv()

API_ID = os.getenv("API_ID")  
API_HASH = os.getenv("API_HASH")

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


@client.on(events.NewMessage(outgoing=True))
async def handler(event):
    """outgoing=True means: only messages YOU sent, in ANY chat.
    This is what lets it work across every group/chat without
    needing to specify one."""
    message = event.message

    is_video = message.video or (
        message.file and message.file.mime_type and "video" in message.file.mime_type
    )
    if not is_video:
        return
    if message.text:  # already has a caption, leave it alone
        return

    filename = get_filename(message)
    caption = filename_to_caption(filename)

    try:
        await message.edit(text=caption)
        chat = await event.get_chat()
        chat_name = getattr(chat, "title", None) or getattr(chat, "first_name", "Unknown")
        print(f"  Captioned '{filename}' -> '{caption}'  (in: {chat_name})")
    except Exception as e:
        print(f"  FAILED to caption message {message.id}: {e}")


async def main():
    await client.start()
    print("Logged in successfully.")
    print("Watching ALL your chats for new captionless videos... (Ctrl+C to stop)")
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\nStopped by user. Goodbye!")