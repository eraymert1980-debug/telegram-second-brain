import os
import sys
import logging
import asyncio
import httpx
from datetime import datetime
from dotenv import load_dotenv

# Ortam değişkenlerini yükle (.env dosyası varsa)
load_dotenv()

from telegram import Update
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import db
import ai_engine

# Loglama ayarları
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.environ.get("ALLOWED_USER_ID")
if ALLOWED_USER_ID:
    try:
        ALLOWED_USER_ID = int(ALLOWED_USER_ID)
    except ValueError:
        ALLOWED_USER_ID = None

def is_authorized(user_id: int) -> bool:
    if not ALLOWED_USER_ID:
        return True
    return user_id == ALLOWED_USER_ID

async def check_auth(update: Update) -> bool:
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ Bu bot kişisel bir asistandır ve yabancı erişimine kapalıdır.")
        return False
    return True

# --- Yardımcı: Telegram dosyasını doğrudan httpx ile indir ---
async def download_telegram_file(bot, file_id: str, dest_path: str):
    """Telegram dosyasını python-telegram-bot yerine doğrudan httpx ile indirir."""
    file_obj = await bot.get_file(file_id)
    file_url = file_obj.file_path
    # file_path zaten tam URL olabilir (https://api.telegram.org/file/bot.../...)
    if not file_url.startswith("http"):
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_url}"
    
    logger.info(f"Dosya indiriliyor: {file_url}")
    
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=60.0)) as client:
        response = await client.get(file_url)
        response.raise_for_status()
        with open(dest_path, "wb") as f:
            f.write(response.content)
    
    logger.info(f"Dosya başarıyla indirildi: {dest_path} ({os.path.getsize(dest_path)} bytes)")

# --- Komutlar ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"/start komutu: Kullanıcı {update.effective_user.id} ({update.effective_user.username})")
    if not await check_auth(update):
        return
    user = update.effective_user
    welcome_text = (
        f"👋 Merhaba {user.first_name}!\n\n"
        "Ben senin **Kişisel Hafıza ve Akıllı Hatırlatıcı (Second Brain)** asistanınım.\n\n"
        "**Neler Yapabilirim?**\n"
        "• 🎙️ **Sesli Not:** Ses kaydı at; anında deşifre edip ister not alayım, ister hatırlatıcı kurayım.\n"
        "• 📷 **Fotoğraf/Belge:** Anahtarı nereye koyduğunu, fişleri, kartları fotoğrafla; hafızaya alayım.\n"
        "• ✍️ **Yazılı Not:** 'KuveytTürk müşteri no: 123456' gibi bilgileri bana fırlat.\n"
        "• 🔔 **Akıllı Hatırlatma:** 'Salı 14:00'te şunu hatırlat' veya 'Yarın sabah ilacı iç' de, vakti gelince bildireyim.\n"
        "• 🔍 **Sorgulama:** 'Anahtarı nereye saklamıştım?', 'Müşteri numaram neydi?' diye sor, hemen bulup söyleyeyim.\n\n"
        "**Faydalı Komutlar:**\n"
        "/notlar - Son kaydettiğin notlar\n"
        "/hatirlaticilar - Bekleyen aktif hatırlatıcıların\n"
        "/yedek - Hafıza veritabanının yedeğini indir\n"
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

async def notes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    user_id = update.effective_user.id
    recent_notes = db.get_recent_notes(user_id, limit=5)
    if not recent_notes:
        await update.message.reply_text("Henüz kayıtlı bir notun yok.")
        return
    text = "📚 **Son Eklenen Notların:**\n\n"
    for n in recent_notes:
        text += f"• *[{n['created_at'][:16]}]* ({n['msg_type']}): {n['content'][:120]}...\n\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def reminders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    user_id = update.effective_user.id
    reminders = db.get_active_reminders(user_id)
    if not reminders:
        await update.message.reply_text("Bekleyen aktif bir hatırlatıcın bulunmuyor.")
        return
    text = "⏰ **Bekleyen Hatırlatıcıların:**\n\n"
    for r in reminders:
        text += f"🔔 *Zaman:* {r['remind_at']}\n📌 *Görev:* {r['content']}\n\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    if os.path.exists(db.DB_PATH):
        await update.message.reply_document(
            document=open(db.DB_PATH, "rb"),
            filename=f"brain_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.db",
            caption="📦 İşte kişisel hafıza veritabanının güncel yedeği."
        )
    else:
        await update.message.reply_text("Veritabanı henüz oluşturulmamış.")

# --- İçerik Analiz ve Yönlendirme ---
async def process_text_intent(update: Update, text: str, user_id: int):
    parsed = ai_engine.classify_and_parse(text)
    action = parsed.get("action")
    
    if action == "reminder":
        remind_at = parsed.get("remind_at")
        content = parsed.get("content")
        db.add_reminder(user_id, remind_at, content)
        await update.message.reply_text(
            f"⏰ **Hatırlatıcı Ayarlandı!**\n\n"
            f"📅 **Zaman:** {remind_at}\n"
            f"📌 **Görev:** {content}\n\n"
            "Vakti geldiğinde sana buradan bildirim atacağım.",
            parse_mode=ParseMode.MARKDOWN
        )
    elif action == "query":
        search_keywords = parsed.get("search_keywords", text)
        notes = db.search_notes(user_id, search_keywords, limit=5)
        answer = ai_engine.answer_query(text, notes)
        await update.message.reply_text(answer)
    else:
        summary = parsed.get("summary", text)
        tags = parsed.get("tags", "")
        db.add_note(user_id, "text", text, summary, tags)
        await update.message.reply_text(
            f"📝 **Hafızaya Kaydedildi!**\n\n"
            f"_{summary}_\n"
            f"🏷️ Etiketler: `{tags}`",
            parse_mode=ParseMode.MARKDOWN
        )

# --- Metin Mesaj İşleyici ---
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Metin: {update.message.text} (User: {update.effective_user.id})")
    if not await check_auth(update):
        return
    user_id = update.effective_user.id
    user_text = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        await process_text_intent(update, user_text, user_id)
    except Exception as e:
        logger.error(f"Metin hata: {e}", exc_info=True)
        await update.message.reply_text(f"⚠️ Bir hata oluştu: {e}")

# --- Sesli Mesaj İşleyici ---
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    user_id = update.effective_user.id
    logger.info(f"Sesli mesaj alındı, kullanıcı: {user_id}")
    
    status_msg = await update.message.reply_text("⏳ Ses alınıyor ve indiriliyor...")
    
    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
    os.makedirs(temp_dir, exist_ok=True)
    temp_audio_path = None
    
    try:
        voice = update.message.voice or update.message.audio
        temp_audio_path = os.path.join(temp_dir, f"voice_{voice.file_id}.ogg")
        
        # Doğrudan httpx ile indir (python-telegram-bot'un download metodunu atla)
        await download_telegram_file(context.bot, voice.file_id, temp_audio_path)
        
        await status_msg.edit_text("⏳ Ses yapay zeka ile çözümleniyor...")
        
        transcribed_text = ai_engine.transcribe_audio(temp_audio_path)
        
        await status_msg.edit_text(f"🎙️ *Ses Deşifresi:*\n\"{transcribed_text}\"", parse_mode=ParseMode.MARKDOWN)
        
        await process_text_intent(update, transcribed_text, user_id)
    except Exception as e:
        logger.error(f"Ses hatası: {type(e).__name__}: {e}", exc_info=True)
        try:
            await status_msg.edit_text(f"⚠️ Ses hatası [{type(e).__name__}]: {e}")
        except Exception:
            await update.message.reply_text(f"⚠️ Ses hatası [{type(e).__name__}]: {e}")
    finally:
        if temp_audio_path and os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)

# --- Fotoğraf İşleyici ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    user_id = update.effective_user.id
    logger.info(f"Fotoğraf alındı, kullanıcı: {user_id}")
    
    status_msg = await update.message.reply_text("⏳ Fotoğraf alınıyor ve inceleniyor...")
    
    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
    os.makedirs(temp_dir, exist_ok=True)
    temp_img_path = None
    
    try:
        photo = update.message.photo[-1]
        caption = update.message.caption or ""
        temp_img_path = os.path.join(temp_dir, f"img_{photo.file_id}.jpg")
        
        await download_telegram_file(context.bot, photo.file_id, temp_img_path)
        
        await status_msg.edit_text("⏳ Fotoğraf yapay zeka ile analiz ediliyor...")
        
        analysis = ai_engine.analyze_image(temp_img_path, caption)
        db.add_note(user_id, "photo", analysis, summary=analysis[:100], tags="görsel,fotoğraf")
        
        await status_msg.edit_text(
            f"📷 **Görsel İncelendi ve Hafızaya Eklendi!**\n\n{analysis}",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Eğer fotoğrafın altında bir hatırlatma/istek yazıyorsa onu da işle!
        if caption:
            try:
                parsed_cap = ai_engine.classify_and_parse(caption)
                if parsed_cap.get("action") == "reminder":
                    remind_at = parsed_cap.get("remind_at")
                    first_lines = "\n".join(analysis.split("\n")[:4])
                    reminder_content = f"Görseldeki Notlar: {first_lines}"
                    db.add_reminder(user_id, remind_at, reminder_content)
                    await update.message.reply_text(
                        f"⏰ **Hatırlatıcı da Ayarlandı!**\n\n"
                        f"📅 **Zaman:** {remind_at}\n"
                        f"📌 **Görev:** Bu fotoğraftaki notlar sana zamanında hatırlatılacak.",
                        parse_mode=ParseMode.MARKDOWN
                    )
            except Exception as cap_err:
                logger.error(f"Fotoğraf açıklaması işlenirken hata: {cap_err}")
    except Exception as e:
        logger.error(f"Görsel hatası: {e}", exc_info=True)
        try:
            await status_msg.edit_text(f"⚠️ Görsel hatası [{type(e).__name__}]: {e}")
        except Exception:
            await update.message.reply_text(f"⚠️ Görsel hatası [{type(e).__name__}]: {e}")
    finally:
        if temp_img_path and os.path.exists(temp_img_path):
            os.remove(temp_img_path)

# --- Hatırlatıcı Kontrolcüsü ---
async def reminder_checker_job(context: ContextTypes.DEFAULT_TYPE):
    due_items = db.get_due_reminders()
    for item in due_items:
        user_id = item["user_id"]
        content = item["content"]
        reminder_id = item["id"]
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🔔 **HATIRLATMA ZAMANI!**\n\n📌 {content}",
                parse_mode=ParseMode.MARKDOWN,
                disable_notification=False
            )
            db.mark_reminder_completed(reminder_id)
            logger.info(f"Hatırlatıcı iletildi: ID {reminder_id} -> User {user_id}")
        except Exception as e:
            logger.error(f"Hatırlatıcı hatası (ID {reminder_id}): {e}")

# --- Render Web Sunucusu (Uyku Engelleme) ---
from aiohttp import web

async def health_check(request):
    return web.Response(text="Second Brain Bot is running 7/24!")

async def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web sunucusu {port} portunda başlatıldı.")

async def post_init(application: Application):
    if application.job_queue:
        application.job_queue.run_repeating(reminder_checker_job, interval=30, first=5)
        logger.info("Hatırlatıcı job'ı başlatıldı.")
    asyncio.create_task(start_web_server())

# --- Ana Giriş ---
def main():
    db.init_db()
    
    if not TELEGRAM_BOT_TOKEN:
        print("\n❌ TELEGRAM_BOT_TOKEN bulunamadı!")
        sys.exit(1)
        
    req = HTTPXRequest(
        connection_pool_size=20,
        read_timeout=120.0,
        write_timeout=120.0,
        connect_timeout=60.0,
        pool_timeout=60.0
    )
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).request(req).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("notlar", notes_cmd))
    app.add_handler(CommandHandler("hatirlaticilar", reminders_cmd))
    app.add_handler(CommandHandler("yedek", backup_cmd))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    print("🚀 Telegram Second Brain Botu başlatıldı ve dinliyor...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
