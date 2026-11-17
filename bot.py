import os
import sys
import logging
import asyncio
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
    """Kullanıcının bota erişim yetkisi var mı?"""
    if not ALLOWED_USER_ID:
        # Eğer henüz ALLOWED_USER_ID ayarlanmadıysa ilk yazan kişiyi kabul edebiliriz
        return True
    return user_id == ALLOWED_USER_ID

async def check_auth(update: Update) -> bool:
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ Bu bot kişisel bir asistandır ve yabancı erişimine kapalıdır.")
        return False
    return True

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start komutu"""
    logger.info(f"/start komutu alındı: Kullanıcı {update.effective_user.id} ({update.effective_user.username})")
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
    """/notlar komutu"""
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
    """/hatirlaticilar komutu"""
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
    """/yedek komutu: SQLite dosyasını Telegram'dan atar."""
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

async def process_text_intent(update: Update, text: str, user_id: int):
    """Metni AI ile analiz edip gerekli aksiyonu (not / sorgu / hatırlatıcı) çalıştırır."""
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
    else: # note
        summary = parsed.get("summary", text)
        tags = parsed.get("tags", "")
        db.add_note(user_id, "text", text, summary, tags)
        await update.message.reply_text(
            f"📝 **Hafızaya Kaydedildi!**\n\n"
            f"_{summary}_\n"
            f"🏷️ Etiketler: `{tags}`",
            parse_mode=ParseMode.MARKDOWN
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Metin mesajları işleyici"""
    logger.info(f"Gelen metin mesajı: {update.message.text} (Kullanıcı: {update.effective_user.id})")
    if not await check_auth(update):
        return
    user_id = update.effective_user.id
    user_text = update.message.text
    
    # Kullanıcıya işlem yapıldığını hissettirmek için typing aksiyonu
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        await process_text_intent(update, user_text, user_id)
    except Exception as e:
        logger.error(f"Hata: {e}", exc_info=True)
        await update.message.reply_text(f"⚠️ Bir hata oluştu: {e}")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sesli mesaj işleyici (Groq Whisper)"""
    if not await check_auth(update):
        return
    user_id = update.effective_user.id
    logger.info(f"Sesli mesaj alındı, kullanıcı: {user_id}")
    
    status_msg = await update.message.reply_text("⏳ Ses alınıyor ve indiriliyor...")
    
    temp_dir = os.path.join(os.path.dirname(__file__), "temp")
    os.makedirs(temp_dir, exist_ok=True)
    temp_audio_path = None
    
    try:
        voice = update.message.voice or update.message.audio
        file = await context.bot.get_file(voice.file_id, read_timeout=120.0, write_timeout=120.0, connect_timeout=60.0)
        temp_audio_path = os.path.join(temp_dir, f"voice_{voice.file_id}.ogg")
        await file.download_to_drive(temp_audio_path, read_timeout=120.0, write_timeout=120.0)
        
        await status_msg.edit_text("⏳ Ses yapay zeka ile çözümleniyor (Groq Whisper)...")
        
        # Groq Whisper ile deşifre et
        transcribed_text = ai_engine.transcribe_audio(temp_audio_path)
        
        await status_msg.edit_text(f"🎙️ *Ses Deşifresi:*\n\"{transcribed_text}\"", parse_mode=ParseMode.MARKDOWN)
        
        # Deşifre edilen metni intent motoruna sok
        await process_text_intent(update, transcribed_text, user_id)
    except Exception as e:
        logger.error(f"Ses işleme hatası: {type(e).__name__}: {e}", exc_info=True)
        await status_msg.edit_text(f"⚠️ Ses işlenirken hata oluştu [{type(e).__name__}]: {e}")
    finally:
        if temp_audio_path and os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fotoğraf işleyici (Gemini / Vision)"""
    if not await check_auth(update):
        return
    user_id = update.effective_user.id
    logger.info(f"Fotoğraf alındı, kullanıcı: {user_id}")
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="upload_photo")
    
    temp_dir = os.path.join(os.path.dirname(__file__), "temp")
    os.makedirs(temp_dir, exist_ok=True)
    temp_img_path = None
    
    try:
        photo = update.message.photo[-1]
        caption = update.message.caption or ""
        file = await context.bot.get_file(photo.file_id, read_timeout=120.0, write_timeout=120.0, connect_timeout=60.0)
        temp_img_path = os.path.join(temp_dir, f"img_{photo.file_id}.jpg")
        await file.download_to_drive(temp_img_path, read_timeout=120.0, write_timeout=120.0)
        
        analysis = ai_engine.analyze_image(temp_img_path, caption)
        db.add_note(user_id, "photo", analysis, summary=analysis[:100], tags="görsel,fotoğraf")
        await update.message.reply_text(
            f"📷 **Görsel İncelendi ve Hafızaya Eklendi!**\n\n"
            f"{analysis}",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Görsel işleme hatası: {e}", exc_info=True)
        await update.message.reply_text(f"⚠️ Görsel işlenirken bir sorun oluştu: {e}")
    finally:
        if temp_img_path and os.path.exists(temp_img_path):
            os.remove(temp_img_path)

async def reminder_checker_job(context: ContextTypes.DEFAULT_TYPE):
    """Her 30 saniyede bir vakti gelen hatırlatıcıları kontrol eder ve gönderir."""
    due_items = db.get_due_reminders()
    for item in due_items:
        user_id = item["user_id"]
        content = item["content"]
        reminder_id = item["id"]
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🔔 **HATIRLATMA ZAMANI!**\n\n📌 {content}",
                parse_mode=ParseMode.MARKDOWN
            )
            db.mark_reminder_completed(reminder_id)
            logger.info(f"Hatırlatıcı iletildi: ID {reminder_id} -> User {user_id}")
        except Exception as e:
            logger.error(f"Hatırlatıcı gönderme hatası (ID {reminder_id}): {e}")

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
    logger.info(f"Render uyumlu Web Sunucusu {port} portunda başlatıldı.")

async def post_init(application: Application):
    """Bot başladıktan sonra çalıştırılacak kurulumlar (JobQueue & Web Server)"""
    if application.job_queue:
        application.job_queue.run_repeating(reminder_checker_job, interval=30, first=5)
        logger.info("Hatırlatıcı job'ı başlatıldı.")
    
    # Render için HTTP Web Server başlat
    asyncio.create_task(start_web_server())

def main():
    # Veritabanını hazırla
    db.init_db()
    
    if not TELEGRAM_BOT_TOKEN:
        print("\n❌ TELEGRAM_BOT_TOKEN bulunamadı!")
        print("Lütfen bir .env dosyası oluşturup veya terminalden TELEGRAM_BOT_TOKEN değişkenini tanımlayın.")
        print("Örnek: export TELEGRAM_BOT_TOKEN='123456:ABC-DEF...'")
        sys.exit(1)
        
    req = HTTPXRequest(
        connection_pool_size=20,
        read_timeout=120.0,
        write_timeout=120.0,
        connect_timeout=60.0,
        pool_timeout=60.0
    )
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).request(req).post_init(post_init).build()
    
    # Komutlar
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("notlar", notes_cmd))
    app.add_handler(CommandHandler("hatirlaticilar", reminders_cmd))
    app.add_handler(CommandHandler("yedek", backup_cmd))
    
    # Mesajlar
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    print("🚀 Telegram Second Brain Botu başlatıldı ve dinliyor...")
    app.run_polling(drop_pending_updates=False)

if __name__ == "__main__":
    main()

