import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import ContextTypes
from config import logger
import io
import zipfile


async def fetch_api(url: str, payload: dict = None, timeout: float = 120.0, surface_errors: bool = False) -> dict:
    try:
        async with httpx.AsyncClient() as client:
            if payload is not None:
                response = await client.post(url, json=payload, timeout=timeout)
            else:
                response = await client.get(url, timeout=timeout)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        msg = None
        try:
            body = e.response.json()
            msg = body.get("error") or body.get("detail")
        except Exception:
            pass
        logger.error(f"API HTTP {e.response.status_code} at {url}: {msg or e}")
        if surface_errors:
            return {"error": msg or f"Server returned HTTP {e.response.status_code}"}
        return {}
    except Exception as e:
        logger.error(f"API JSON Error at {url}: {e}")
        return {"error": "Could not reach the server."} if surface_errors else {}




async def fetch_api_raw(url: str, timeout: float = 120.0) -> bytes:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=timeout)
            response.raise_for_status()
            return response.content
    except Exception as e:
        logger.error(f"API RAW Error at {url}: {e}")
        return None



async def unzip_and_send(update: Update, wait_msg, zip_bytes: bytes, title: str):
    
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            media_group = []
            for filename in z.namelist():
                if filename.endswith(".png"):
                    img_data = z.read(filename)
                    media_group.append(InputMediaPhoto(media=img_data))

            if not media_group:
                await wait_msg.edit_text("⚠️ The ZIP archive is empty or contains no PNGs.")
                return
            
            chunks = [media_group[i:i + 10] for i in range(0, len(media_group), 10)]
            for idx, chunk in enumerate(chunks):
                if idx == 0:
                    chunk[0] = InputMediaPhoto(
                        media=chunk[0].media,
                        caption=f"📊 **{title}**",
                        parse_mode='Markdown'
                    )
                await update.get_bot().send_media_group(chat_id=wait_msg.chat_id, media=chunk)
                
            await wait_msg.delete()
    except Exception as e:
        await wait_msg.edit_text(f"⚠️ Error extracting ZIP: {e}")





def build_keyboard(buttons: list[list[tuple[str, str]]], back_data: str = None) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(text, callback_data=data) for text, data in row] for row in buttons]
    if back_data:
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=back_data)])
    return InlineKeyboardMarkup(keyboard)


async def check_spam_lock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if context.user_data.get('is_processing'):
        await update.callback_query.answer("⏳ An operation is already in progress! Please wait...", show_alert=True)
        return True
    
    context.user_data['is_processing'] = True
    
    return False