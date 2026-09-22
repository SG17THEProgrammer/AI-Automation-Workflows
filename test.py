import asyncio
from telethon import TelegramClient
from telethon.errors import PhoneNumberInvalidError, FloodWaitError
import os
from dotenv import load_dotenv

load_dotenv()

API_ID = os.getenv("API_ID")  
API_HASH = os.getenv("API_HASH")

PHONE = os.getenv("PHONE")  # your number with country code

client = TelegramClient("test_session", API_ID, API_HASH)

async def main():
    await client.connect()
    print("Connected:", client.is_connected())
    try:
        result = await client.send_code_request(PHONE, force_sms=True)
        print("Code request sent. Result:", result)
    except PhoneNumberInvalidError:
        print("Phone number format is invalid.")
    except FloodWaitError as e:
        print(f"Rate limited. Wait {e.seconds} seconds before trying again.")
    except Exception as e:
        print("Error:", type(e).__name__, e)
    await client.disconnect()

asyncio.run(main())