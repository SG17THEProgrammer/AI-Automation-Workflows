import asyncio
from telethon import TelegramClient, functions, types, errors
import os
from dotenv import load_dotenv

load_dotenv()

API_ID = os.getenv("API_ID")  
API_HASH = os.getenv("API_HASH")


async def delete_archived_supergroups():
    async with TelegramClient("archived_cleaner_session", API_ID, API_HASH) as client:
        print("Scanning strictly for ARCHIVED supergroups...\n")

        async for dialog in client.iter_dialogs():
            # Skip any chat that is not strictly in the Archive folder
            if not dialog.archived:
                continue

            entity = dialog.entity

            # Check if the archived chat is a Supergroup / Megagroup
            if isinstance(entity, types.Channel) and entity.megagroup:
                group_title = dialog.name
                is_owner = getattr(entity, "creator", False)
                action_desc = "DELETE PERMANENTLY (Owner)" if is_owner else "LEAVE & REMOVE (Member)"

                print("=" * 60)
                print(f"CONFIRMED ARCHIVED SUPERGROUP: {group_title} (ID: {entity.id})")
                print(f"Action Type:                  {action_desc}")
                print("=" * 60)

                # Prompt for manual confirmation
                prompt = f"Delete/Leave archived group '{group_title}'? [Press Enter or 'y' to CONFIRM, any other key to SKIP]: "
                user_choice = input(prompt).strip().lower()

                if user_choice in ["", "y", "yes"]:
                    while True:
                        try:
                            if is_owner:
                                await client(functions.channels.DeleteChannelRequest(channel=entity))
                                print(f"--> Successfully DELETED: {group_title}\n")
                            else:
                                await client(functions.channels.LeaveChannelRequest(channel=entity))
                                await client.delete_dialog(entity)
                                print(f"--> Successfully LEFT & REMOVED: {group_title}\n")
                            break  # Exit retry loop on success
                        except errors.FloodWaitError as e:
                            wait_mins = round(e.seconds / 60, 1)
                            print(f"\n⚠️ Telegram Rate Limit Hit! Must wait {e.seconds} seconds (~{wait_mins} min).")
                            print("Pausing execution... The script will automatically resume after the wait period.\n")
                            await asyncio.sleep(e.seconds + 2)
                        except Exception as e:
                            print(f"--> Failed to process {group_title}: {e}\n")
                            break

                    # Small delay between deletions to reduce rate-limit triggers
                    await asyncio.sleep(3)
                else:
                    print(f"--> SKIPPED (Unchanged): {group_title}\n")

        print("Finished processing archived supergroups.")


if __name__ == "__main__":
    asyncio.run(delete_archived_supergroups())