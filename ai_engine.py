import os
import json
import base64
import time
from datetime import datetime
from groq import Groq

# Groq Client (Geniş timeout ve otomatik 3 tekrar deneme ile)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_API_KEY, timeout=90.0, max_retries=3) if GROQ_API_KEY else None

# Gemini Client (opsiyonel görsel ve video için)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
genai_client = None
if GEMINI_API_KEY:
    try:
        from google import genai
        genai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"Gemini client başlatılamadı: {e}")

def transcribe_audio(audio_path: str) -> str:
    """Ses kaydını (OGG/MP3/M4A) Groq Whisper ile Türkçe metne dönüştürür."""
    if not groq_client:
        raise ValueError("GROQ_API_KEY ayarlanmamış!")
    
    with open(audio_path, "rb") as file_obj:
        transcription = groq_client.audio.transcriptions.create(
            file=(os.path.basename(audio_path), file_obj),
            model="whisper-large-v3-turbo",
            language="tr",
            response_format="text"
        )
    return str(transcription).strip()

def analyze_media(file_path: str, mime_type: str = "image/jpeg", caption: str = "") -> str:
    """Görsel veya PDF belgesini ultra hızlı Gemini Vision modelleri ile analiz eder."""
    if not genai_client:
        fallback_desc = f"Dosya ({mime_type}) kaydedildi."
        if caption:
            fallback_desc += f" Kullanıcı notu: {caption}"
        return fallback_desc

    from google.genai import types

    is_pdf = (mime_type == "application/pdf")
    if is_pdf:
        prompt = (
            "Bu PDF belgesini (taranmış kimlik, pasaport, ehliyet, fatura, sözleşme veya resmi evrak) kişisel bir hafıza asistanı olarak ayrıntılı incele:\n"
            "- Belge tipini belirt (Örn: T.C. Kimlik Kartı, Sürücü Belgesi, Fatura vb.).\n"
            "- İsim, soyisim, T.C. kimlik numarası, doğum tarihi, seri no, veriliş tarihi gibi tüm kimlik ve belge bilgilerini eksiksiz madde madde listele.\n"
            "- Varsa adres, tutar, son geçerlilik tarihi ve barkod/karekod altındaki kodları da ekle.\n"
            "Gereksiz ortam tasviri veya süsleme yapma, doğrudan hafızaya alınacak gerçek bilgileri net ve aranabilir şekilde yaz."
        )
    else:
        # Hız Optimizasyonu: Dolgu cümlelerini engelle, doğrudan net bilgileri maddeler halinde çıkar
        prompt = (
            "Görseldeki önemli bilgileri kompakt, net ve doğrudan maddeler halinde çıkar:\n"
            "- Belge/Defter/Fiş varsa: Tüm başlıkları, el yazılarını, isimleri ve numaraları madde madde listele.\n"
            "- Eşya/Konum varsa: Eşyanın nerede olduğunu net olarak belirt.\n"
            "Gereksiz ortam tasviri veya süslemeler yapma, doğrudan hafızaya alınacak gerçek bilgileri yaz."
        )
    if caption:
        prompt += f"\nKullanıcının bu belgeye/fotoğrafa eklediği not: '{caption}'"

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)

    # Sadece güncel ve resmi aktif Gemini 3 modelleri (ve 503 yoğunluk dalgalanması koruması)
    models_to_try = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3-flash-preview"]
    errors = []

    for m in models_to_try:
        for attempt in range(2):
            try:
                response = genai_client.models.generate_content(
                    model=m,
                    contents=[part, prompt]
                )
                if response and response.text:
                    return response.text.strip()
            except Exception as e:
                err_str = str(e)
                if ("503" in err_str or "UNAVAILABLE" in err_str) and attempt == 0:
                    time.sleep(1.5)
                    continue
                errors.append(f"{m}: {e}")
                break

    raise RuntimeError(" | ".join(errors))

def analyze_image(image_path: str, caption: str = "") -> str:
    """Geriye dönük uyumluluk için görsel analiz wrapper'ı."""
    return analyze_media(image_path, mime_type="image/jpeg", caption=caption)

def classify_and_parse(text: str, current_time: datetime = None) -> dict:
    """
    Kullanıcının yazdığı veya sesle söylediği cümleyi inceler:
    1. 'reminder': Eğer zaman belirtip bir şey hatırlatılmasını istiyorsa.
    2. 'query': Eğer geçmiş bir notu/bilgiyi soruyorsa.
    3. 'note': Yeni bir bilgi/not kaydediyorsa.
    """
    if current_time is None:
        current_time = datetime.now()
        
    current_time_str = current_time.strftime("%Y-%m-%d %H:%M:%S (%A)")
    
    system_prompt = f"""
Sen kullanıcının şahsi Kişisel Hafıza (Second Brain) asistanısın.
Şu anki gerçek zaman: {current_time_str}.
Kullanıcı kendi şahsi verilerini (TC kimlik no, müşteri no, anahtar yeri, hatırlatma vb.) yönetmektedir.
Bu meşru bir kişisel not sistemidir, KESİNLİKLE reddetme, 'yardım edemem' deme.

Kullanıcıdan gelen mesajı analiz et ve SADECE geçerli bir JSON objesi döndür:

1. Eğer kullanıcı gelecekte bir şeyi hatırlatmanı istiyorsa:
{{
    "action": "reminder",
    "remind_at": "YYYY-MM-DD HH:MM:00",
    "content": "Hatırlatılacak görev özeti"
}}

2. Eğer kullanıcı geçmişe dair bir soru soruyorsa (Örn: "TC kimlik numaram neydi?", "Anahtar neredeydi?", "Müşteri numaram kaç?"):
{{
    "action": "query",
    "search_keywords": "aranacak anahtar kelimeler",
    "question": "kullanıcının orijinal sorusu"
}}

3. Eğer kullanıcı bir hatırlatıcıyı iptal etmek veya silmek istiyorsa (Örn: "hatırlatmayı iptal et", "son hatırlatmayı sil", "vazgeçtim hatırlatma"):
{{
    "action": "cancel_reminder"
}}

4. Eğer kullanıcı son kaydedilen nottaki bir hatayı düzeltmek/güncellemek istiyorsa (Örn: "Roptürk değil Koptur olacak", "Son notu düzelt", "Orası yanlış şu şekilde değiştir"):
{{
    "action": "correction",
    "instruction": "kullanıcının düzeltme talimatı"
}}

5. Eğer kullanıcı yeni bir bilgi veya not kaydediyorsa:
{{
    "action": "note",
    "summary": "Tek cümlelik hafıza özeti",
    "tags": "etiketler"
}}
"""
    # 1. Önce Groq LPU ile ultra hızlı niyet analizi (300 ms)
    if groq_client:
        for model_name in ["qwen/qwen3.8-27b", "openai/gpt-oss-20b", "openai/gpt-oss-120b"]:
            try:
                response = groq_client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text}
                    ],
                    temperature=0.1,
                    response_format={"type": "json_object"}
                )
                raw_content = response.choices[0].message.content
                if raw_content:
                    return json.loads(raw_content)
            except Exception:
                continue

    # 2. Groq meşgulse Gemini ile dene (Yedek)
    if genai_client:
        models_to_try = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3-flash-preview"]
        for m in models_to_try:
            try:
                resp = genai_client.models.generate_content(
                    model=m,
                    contents=f"{system_prompt}\n\nKullanıcı Mesajı: {text}",
                    config={"response_mime_type": "application/json"}
                )
                if resp and resp.text:
                    return json.loads(resp.text)
            except Exception:
                continue

    # 3. Fallback: Eğer modeller JSON üretmeyi reddederse akıllı sezgisel yönlendirme (0 ms)
    lower_text = text.lower()
    if any(q in lower_text for q in ["neydi", "kaçtı", "nerede", "kimdi", "ne zaman", "hangisi", "?"]):
        return {"action": "query", "search_keywords": text, "question": text}
    elif any(w in lower_text for w in ["iptal", "vazgeç", "sil"]) and any(w in lower_text for w in ["hatırlat", "alarm"]):
        return {"action": "cancel_reminder"}
    elif any(w in lower_text for w in ["düzelt", "değil", "yanlış okudun", "şöyle olacak", "olarak değiştir"]):
        return {"action": "correction", "instruction": text}
    elif any(r in lower_text for r in ["hatırlat", "alarm", "sonra", "yarın", "saat"]):
        return {"action": "reminder", "remind_at": current_time.strftime("%Y-%m-%d %H:%M:00"), "content": text}
    else:
        return {"action": "note", "summary": text, "tags": "genel"}

def correct_note_content(original_content: str, correction_instruction: str) -> str:
    """
    Kullanıcının talimatına göre mevcut nottaki yanlış yazılmış/okunmuş yeri akıllıca düzeltir.
    Örn: 'Roptürk değil Koptur faturası olacak' -> Roptürk yerine Koptur faturası koyar.
    Ultra hızlı Groq LPU (200 ms) kullanır.
    """
    prompt = (
        "Sen kişisel bir not düzenleyicisisin.\n\n"
        f"MEVCUT NOT METNİ:\n\"\"\"{original_content}\"\"\"\n\n"
        f"KULLANICININ DÜZELTME TALEBİ:\n\"{correction_instruction}\"\n\n"
        "GÖREVİN:\n"
        "Mevcut not metninde kullanıcının belirttiği düzeltmeyi yap ve SADECE güncellenmiş notun tam halini döndür.\n"
        "Kurallar:\n"
        "- Başka hiçbir açıklama, selamlama veya 'İşte güncel not:' gibi fazlalık yazma.\n"
        "- Sadece güncellenmiş notun kendisini döndür."
    )
    if groq_client:
        for model in ["qwen/qwen3.8-27b", "openai/gpt-oss-120b"]:
            try:
                resp = groq_client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1
                )
                res_text = resp.choices[0].message.content.strip()
                if res_text:
                    return res_text
            except Exception:
                continue
    return original_content

def answer_query(user_question: str, notes: list) -> str:
    """Bulunan notlara göre kullanıcıya doğal, net ve samimi bir Türkçe yanıt üretir."""
    context_text = ""
    if notes:
        for idx, n in enumerate(notes, 1):
            context_text += f"{idx}. [Tarih: {n['created_at']}, Tip: {n['msg_type']}] {n['content']}\n"
    else:
        context_text = "Hafızada bu konuyla ilgili doğrudan bir kayıt bulunamadı."
        
    prompt = f"""
Sen kullanıcının şahsi kişisel hafıza asistanısın.
Kullanıcı kendi geçmiş notlarından bir soru sordu:
Soru: "{user_question}"

Kullanıcının veritabanından bulunan ilgili notlar:
{context_text}

GÖREVİN:
1. Notlardaki bilgiyi kullanarak doğrudan, net ve kesin cevap ver (Örn: "T.C. Kimlik Numaran: 20471308184").
2. Bu kullanıcının kendi şahsi notudur, güvenlik gerekçesiyle reddetme, bilgiyi doğrudan ver.
3. Notlarda tam yoksa benzerini belirt, kesinlikle uydurma.
"""
    # 1. Önce Groq LPU ile yıldırım hızında cevap üret (0.3 saniye)
    if groq_client:
        for model_name in ["qwen/qwen3.8-27b", "openai/gpt-oss-120b"]:
            try:
                response = groq_client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                )
                text_ans = response.choices[0].message.content.strip()
                if text_ans and "I'm sorry" not in text_ans and "cannot help" not in text_ans:
                    return text_ans
            except Exception:
                continue

    # 2. Eğer Groq sansüre takılırsa veya hata verirse Gemini Flash ile üret
    if genai_client:
        models_to_try = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3-flash-preview"]
        for m in models_to_try:
            try:
                resp = genai_client.models.generate_content(
                    model=m,
                    contents=prompt
                )
                if resp and resp.text:
                    return resp.text.strip()
            except Exception:
                continue

    return "Hafızada ilgili kayıt bulundu ancak cevap oluşturulamadı."
