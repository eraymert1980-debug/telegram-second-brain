import os
import sys
import logging
import asyncio
import httpx
from datetime import datetime
from dotenv import load_dotenv

# Ortam değişkenlerini yükle (.env dosyası varsa)
load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
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

from PIL import Image, ImageOps

def optimize_image(input_path: str, max_dimension: int = 1280) -> str:
    """
    Fotoğrafı 1280px sınırına ve hafif JPEG formatına dönüştürerek 
    yapay zekaya aktarımı ve analiz süresini 3-4 kat hızlandırır (yazı netliği bozulmaz).
    """
    try:
        with Image.open(input_path) as img:
            # Telefon çekim yönünü otomatik düzelt (EXIF Orientation)
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            
            # RGB moduna dönüştür
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
                
            w, h = img.size
            if max(w, h) > max_dimension:
                scale = max_dimension / float(max(w, h))
                new_w, new_h = int(w * scale), int(h * scale)
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                
            opt_path = input_path.replace(".jpg", "_opt.jpg")
            img.save(opt_path, format="JPEG", quality=80, optimize=True)
            logger.info(f"Görsel optimize edildi: {input_path} -> {opt_path} ({os.path.getsize(opt_path)} bytes)")
            return opt_path
    except Exception as e:
        logger.warning(f"Görsel optimizasyon hatası, orijinal kullanılacak: {e}")
        return input_path

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
        "• 📄 **PDF / Taranmış Evrak:** Kamera ile taranmış kimlik, pasaport veya PDF dökümanları at; tüm maddeleri okuyayım.\n"
        "• ✍️ **Yazılı Not:** 'KuveytTürk müşteri no: 123456' gibi bilgileri bana fırlat.\n"
        "• 🔔 **Akıllı Hatırlatma:** 'Salı 14:00'te şunu hatırlat' veya 'Yarın sabah ilacı iç' de, vakti gelince bildireyim.\n"
        "• 🔍 **Sorgulama:** 'Anahtarı nereye saklamıştım?', 'Müşteri numaram neydi?' diye sor, hemen bulup söyleyeyim.\n\n"
        "**Faydalı Komutlar:**\n"
        "/notlar - Son kaydettiğin notlar\n"
        "/hatirlaticilar - Bekleyen aktif hatırlatıcıların\n"
        "/iptal - Hatırlatıcı iptal et (veya /iptal <id>)\n"
        "/duzelt - Son nottaki yanlış yeri düzelt (örn: /duzelt Roptürk yerine Koptur)\n"
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
        
    text = "⏰ **Bekleyen Aktif Hatırlatıcıların:**\n\n"
    keyboard = []
    for r in reminders:
        text += f"🔔 *[#{r['id']}]* {r['remind_at']}\n📌 {r['content']}\n\n"
        keyboard.append([
            InlineKeyboardButton(f"❌ İptal Et: #{r['id']} ({r['remind_at'][:16]})", callback_data=f"cancel_rem_{r['id']}")
        ])
        
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/iptal veya /iptal <id> komutuyla hatırlatıcı iptal etme."""
    if not await check_auth(update):
        return
    user_id = update.effective_user.id
    
    # Doğrudan numara girilmiş mi kontrol et: /iptal 2 veya /iptal #2
    if context.args and len(context.args) > 0:
        arg = context.args[0].replace("#", "")
        if arg.isdigit():
            rem_id = int(arg)
            if db.cancel_reminder(user_id, rem_id):
                await update.message.reply_text(f"✅ **#{rem_id} numaralı hatırlatıcı iptal edildi.**", parse_mode=ParseMode.MARKDOWN)
                return
            else:
                await update.message.reply_text(f"⚠️ #{rem_id} ID'li aktif bir hatırlatıcı bulunamadı.")
                return

    # Argüman yoksa bekleyen aktif hatırlatıcıları buton olarak listele
    reminders = db.get_active_reminders(user_id)
    if not reminders:
        await update.message.reply_text("İptal edilecek bekleyen aktif bir hatırlatıcın bulunmuyor.")
        return

    text = "🗑️ **Hangi hatırlatıcıyı iptal etmek istersin?**\nAşağıdaki butonlara tıklayarak anında iptal edebilirsin:\n\n"
    keyboard = []
    for r in reminders:
        text += f"• *#{r['id']}* - {r['remind_at']} - {r['content'][:45]}\n"
        keyboard.append([
            InlineKeyboardButton(f"❌ İptal Et: #{r['id']}", callback_data=f"cancel_rem_{r['id']}")
        ])
        
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cancel_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Butona tıklayarak hatırlatıcı iptal etme callback işleyicisi."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await query.answer("Yetkisiz işlem!", show_alert=True)
        return
        
    data = query.data
    if data.startswith("cancel_rem_"):
        rem_id = int(data.split("_")[-1])
        if db.cancel_reminder(user_id, rem_id):
            await query.edit_message_text(
                f"✅ **#{rem_id} numaralı hatırlatıcı iptal edildi.**\n\nArtık bu hatırlatma için sana bildirim gelmeyecek.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await query.edit_message_text(f"⚠️ #{rem_id} numaralı hatırlatıcı zaten iptal edilmiş veya süresi geçmiş.")

async def edit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/duzelt komutu ile son nottaki hatayı düzeltme."""
    if not await check_auth(update):
        return
    user_id = update.effective_user.id
    last_note = db.get_last_note(user_id)
    if not last_note:
        await update.message.reply_text("Düzeltilecek kayıtlı bir notun bulunmuyor.")
        return
        
    instruction = " ".join(context.args) if context.args else ""
    if not instruction:
        await update.message.reply_text(
            f"📌 **Hafızadaki Son Notun (ID: #{last_note['id']}):**\n\n{last_note['content']}\n\n"
            "Bu nottaki hatayı düzeltmek için örneğin:\n"
            "`/duzelt Roptürk yerine Koptur faturası olacak` yazabilirsin.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
        
    status_msg = await update.message.reply_text("⏳ Not akıllıca güncelleniyor...")
    updated_content = ai_engine.correct_note_content(last_note["content"], instruction)
    db.update_note(user_id, last_note["id"], updated_content)
    await status_msg.edit_text(
        f"✏️ **Not Başarıyla Güncellendi!** (ID: #{last_note['id']})\n\n{updated_content}",
        parse_mode=ParseMode.MARKDOWN
    )

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
        rem_id = db.add_reminder(user_id, remind_at, content)
        keyboard = [
            [InlineKeyboardButton("❌ Bu Hatırlatmayı İptal Et", callback_data=f"cancel_rem_{rem_id}")]
        ]
        await update.message.reply_text(
            f"⏰ **Hatırlatıcı Ayarlandı!** (ID: #{rem_id})\n\n"
            f"📅 **Zaman:** {remind_at}\n"
            f"📌 **Görev:** {content}\n\n"
            "Vakti geldiğinde sana buradan bildirim atacağım.\n"
            "*(Vazgeçersen aşağıdaki butona basabilir veya `/iptal {rem_id}` yazabilirsin)*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif action == "cancel_reminder":
        last_rem = db.get_last_active_reminder(user_id)
        if not last_rem:
            await update.message.reply_text("İptal edilecek bekleyen aktif bir hatırlatıcın bulunmuyor.")
        else:
            keyboard = [
                [InlineKeyboardButton(f"❌ #{last_rem['id']} Numaralıyı İptal Et", callback_data=f"cancel_rem_{last_rem['id']}")],
            ]
            await update.message.reply_text(
                f"Son bekleyen aktif hatırlatıcın:\n\n"
                f"🔔 **#{last_rem['id']} - {last_rem['remind_at']}**\n"
                f"📌 {last_rem['content']}\n\n"
                f"Bunu iptal etmek istiyor musun?",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    elif action == "correction":
        last_note = db.get_last_note(user_id)
        if not last_note:
            await update.message.reply_text("Düzeltilecek kayıtlı bir notun bulunmuyor.")
            return
        status_msg = await update.message.reply_text("⏳ Not akıllıca güncelleniyor...")
        instruction = parsed.get("instruction", text)
        updated_content = ai_engine.correct_note_content(last_note["content"], instruction)
        db.update_note(user_id, last_note["id"], updated_content)
        await status_msg.edit_text(
            f"✏️ **Hafızadaki Not Güncellendi!** (ID: #{last_note['id']})\n\n{updated_content}",
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
    opt_img_path = None
    
    try:
        photo = update.message.photo[-1]
        caption = update.message.caption or ""
        temp_img_path = os.path.join(temp_dir, f"img_{photo.file_id}.jpg")
        
        await download_telegram_file(context.bot, photo.file_id, temp_img_path)
        
        # Hızlandırma Optimizasyonu: Görseli 1280px'e ve optimize JPEG'e dönüştür (Transfer ve analiz süresi hızlanır)
        opt_img_path = optimize_image(temp_img_path, max_dimension=1280)
        
        await status_msg.edit_text("⏳ Fotoğraf yapay zeka ile analiz ediliyor...")
        
        analysis = ai_engine.analyze_image(opt_img_path, caption)
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
                    rem_id = db.add_reminder(user_id, remind_at, reminder_content)
                    keyboard = [
                        [InlineKeyboardButton("❌ Bu Hatırlatmayı İptal Et", callback_data=f"cancel_rem_{rem_id}")]
                    ]
                    await update.message.reply_text(
                        f"⏰ **Hatırlatıcı da Ayarlandı!** (ID: #{rem_id})\n\n"
                        f"📅 **Zaman:** {remind_at}\n"
                        f"📌 **Görev:** Bu fotoğraftaki notlar sana zamanında hatırlatılacak.\n"
                        "*(Vazgeçersen aşağıdaki butona basabilir veya `/iptal` yazabilirsin)*",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup(keyboard)
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
        if opt_img_path and os.path.exists(opt_img_path) and opt_img_path != temp_img_path:
            os.remove(opt_img_path)

# --- Belge / PDF İşleyici ---
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    user_id = update.effective_user.id
    doc = update.message.document
    caption = update.message.caption or ""
    
    file_name = doc.file_name or "belge"
    mime_type = doc.mime_type or "application/octet-stream"
    file_size = doc.file_size or 0
    
    # 20MB sınır
    if file_size > 20 * 1024 * 1024:
        await update.message.reply_text("⚠️ Dosya boyutu 20MB'tan büyük olamaz.")
        return

    is_pdf = mime_type == "application/pdf" or file_name.lower().endswith(".pdf")
    is_image = mime_type.startswith("image/") or any(file_name.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"])
    
    if not (is_pdf or is_image):
        await update.message.reply_text(f"📄 '{file_name}' dosyası alındı. Şu an için sadece PDF dökümanları (taranmış kimlik, fatura vb.) ve fotoğrafları analiz edebiliyorum.")
        return

    status_msg = await update.message.reply_text(f"⏳ {'PDF belgesi' if is_pdf else 'Görsel'} alınıyor ve inceleniyor...")
    
    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = None
    
    try:
        ext = ".pdf" if is_pdf else ".jpg"
        temp_path = os.path.join(temp_dir, f"doc_{doc.file_id}{ext}")
        await download_telegram_file(context.bot, doc.file_id, temp_path)
        
        await status_msg.edit_text("⏳ Belge yapay zeka ile taranıyor ve tüm detayları çözümleniyor...")
        
        doc_mime = "application/pdf" if is_pdf else "image/jpeg"
        analysis = ai_engine.analyze_media(temp_path, mime_type=doc_mime, caption=caption)
        
        db.add_note(user_id, "document", analysis, summary=f"{file_name}: {analysis[:100]}", tags="belge,pdf,kimlik" if is_pdf else "görsel,belge")
        
        icon = "📄" if is_pdf else "📷"
        await status_msg.edit_text(
            f"{icon} **Belge İncelendi ve Hafızaya Eklendi!**\n\n{analysis}",
            parse_mode=ParseMode.MARKDOWN
        )
        
        if caption:
            try:
                parsed_cap = ai_engine.classify_and_parse(caption)
                if parsed_cap.get("action") == "reminder":
                    remind_at = parsed_cap.get("remind_at")
                    first_lines = "\n".join(analysis.split("\n")[:4])
                    reminder_content = f"Belgedeki Notlar ({file_name}): {first_lines}"
                    rem_id = db.add_reminder(user_id, remind_at, reminder_content)
                    keyboard = [
                        [InlineKeyboardButton("❌ Bu Hatırlatmayı İptal Et", callback_data=f"cancel_rem_{rem_id}")]
                    ]
                    await update.message.reply_text(
                        f"⏰ **Hatırlatıcı da Ayarlandı!** (ID: #{rem_id})\n\n"
                        f"📅 **Zaman:** {remind_at}\n"
                        f"📌 **Görev:** Bu belgedeki bilgiler sana zamanında hatırlatılacak.\n"
                        "*(Vazgeçersen aşağıdaki butona basabilir veya `/iptal` yazabilirsin)*",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
            except Exception as cap_err:
                logger.error(f"Belge açıklaması hatırlatıcı hatası: {cap_err}")
                
    except Exception as e:
        logger.error(f"Belge analiz hatası: {e}", exc_info=True)
        try:
            await status_msg.edit_text(f"⚠️ Belge analiz hatası [{type(e).__name__}]: {e}")
        except Exception:
            await update.message.reply_text(f"⚠️ Belge analiz hatası [{type(e).__name__}]: {e}")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

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

# --- Render Webhook & Web Sunucusu ---
from aiohttp import web

async def health_check(request):
    return web.Response(text="Second Brain Bot is running 7/24 on Webhook!")

async def run_webhook_mode(req: HTTPXRequest, render_url: str):
    port = int(os.environ.get("PORT", 10000))
    webhook_url = f"{render_url.rstrip('/')}/webhook"
    
    ptb_app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(req)
        .updater(None)
        .build()
    )
    
    # Handlers
    ptb_app.add_handler(CommandHandler("start", start_cmd))
    ptb_app.add_handler(CommandHandler("notlar", notes_cmd))
    ptb_app.add_handler(CommandHandler("hatirlaticilar", reminders_cmd))
    ptb_app.add_handler(CommandHandler("iptal", cancel_cmd))
    ptb_app.add_handler(CommandHandler("duzelt", edit_cmd))
    ptb_app.add_handler(CommandHandler("yedek", backup_cmd))
    ptb_app.add_handler(CallbackQueryHandler(cancel_callback_handler))
    
    ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    ptb_app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    ptb_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    ptb_app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    async with ptb_app:
        await ptb_app.start()
        
        # Hatırlatıcı JobQueue başlat
        if ptb_app.job_queue:
            ptb_app.job_queue.run_repeating(reminder_checker_job, interval=30, first=5)
            logger.info("Hatırlatıcı job'ı başlatıldı.")
            
        server = web.Application()
        server.router.add_get("/", health_check)
        server.router.add_get("/health", health_check)
        
        async def telegram_webhook_handler(request):
            try:
                data = await request.json()
                update = Update.de_json(data, ptb_app.bot)
                await ptb_app.update_queue.put(update)
                return web.Response(status=200)
            except Exception as e:
                logger.error(f"Webhook istek hatası: {e}")
                return web.Response(status=500)
                
        server.router.add_post("/webhook", telegram_webhook_handler)
        
        # Telegram sunucusuna webhook adresini kaydet
        await ptb_app.bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
        logger.info(f"🎯 Telegram Webhook bağlandı: {webhook_url}")
        
        runner = web.AppRunner(server)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info(f"🚀 Webhook sunucusu {port} portunda canlı dinliyor!")
        
        while True:
            await asyncio.sleep(3600)

def run_polling_mode(req: HTTPXRequest):
    logger.info("Yerel Polling modunda başlatılıyor...")
    
    async def post_init_local(application: Application):
        if application.job_queue:
            application.job_queue.run_repeating(reminder_checker_job, interval=30, first=5)
            logger.info("Hatırlatıcı job'ı başlatıldı.")
            
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).request(req).post_init(post_init_local).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("notlar", notes_cmd))
    app.add_handler(CommandHandler("hatirlaticilar", reminders_cmd))
    app.add_handler(CommandHandler("iptal", cancel_cmd))
    app.add_handler(CommandHandler("duzelt", edit_cmd))
    app.add_handler(CommandHandler("yedek", backup_cmd))
    app.add_handler(CallbackQueryHandler(cancel_callback_handler))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    print("🚀 Telegram Second Brain Botu yerel Polling ile dinliyor...")
    app.run_polling(drop_pending_updates=True)

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
    
    try:
        render_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("WEBHOOK_URL")
        if render_url:
            asyncio.run(run_webhook_mode(req, render_url))
        else:
            run_polling_mode(req)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot durduruldu.")

if __name__ == "__main__":
    main()
