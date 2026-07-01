import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import logger

async def fetch_api(url: str, payload: dict = None, timeout: float = 120.0) -> dict:
    """
        Fetches JSON from the given absolute URL.
    """
    try:
        async with httpx.AsyncClient() as client:
            if payload is not None:
                response = await client.post(url, json=payload, timeout=timeout)
            else:
                response = await client.get(url, timeout=timeout)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"API JSON Error at {url}: {e}")
        return {}

async def fetch_api_raw(url: str, timeout: float = 120.0) -> bytes:
    """
        Fetches raw bytes (e.g. for ZIP files) from the given URL.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=timeout)
            response.raise_for_status()
            return response.content
    except Exception as e:
        logger.error(f"API RAW Error at {url}: {e}")
        return None



def build_keyboard(buttons: list[list[tuple[str, str]]], back_data: str = None) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(text, callback_data=data) for text, data in row] for row in buttons]
    if back_data:
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=back_data)])
    return InlineKeyboardMarkup(keyboard)


async def check_spam_lock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
        Prevents users from spamming heavy backend processes.
    """
    if context.user_data.get('is_processing'):
        await update.callback_query.answer("⏳ An operation is already in progress! Please wait...", show_alert=True)
        return True
    
    context.user_data['is_processing'] = True
    
    return False