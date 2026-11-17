import os
import json
import base64
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

def analyze_image(image_path: str, caption: str = "") -> str:
    """Görseli Gemini 3.8 Flash ile analiz eder."""
    if not genai_client:
        fallback_desc = "Fotoğraf kaydedildi."
        if caption:
            fallback_desc += f" Kullanıcı notu: {caption}"
        return fallback_desc

    from google.genai import types

    prompt = (
        "Bu görseli bir kişisel hafıza ve not alma asistanı olarak incele. "
        "Görseldeki önemli nesneleri, eşyaların yerlerini, yazıları/kart numaralarını, el yazılarını veya belgeleri "
        "ayrıntılı, net ve aranabilir bir Türkçe ile açıkla. "
        "Özellikle el yazısı veya defter notu varsa tüm maddeleri tek tek oku ve listele."
    )
    if caption:
        prompt += f"\nKullanıcının görselle birlikte ilettiği not: '{caption}'"

    with open(image_path, "rb") as img_file:
        image_bytes = img_file.read()

    part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

    # Modelleri types.Part formatıyla dene (6ec491c'de çalışan yöntem)
    models_to_try = ["gemini-3.8-flash", "gemini-2.0-flash", "gemini-2.5-flash"]
    last_err = None

    for m in models_to_try:
        try:
            response = genai_client.models.generate_content(
                model=m,
                contents=[part, prompt]
            )
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            last_err = e
            continue

    raise last_err

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

3. Eğer kullanıcı yeni bir bilgi veya not kaydediyorsa:
{{
    "action": "note",
    "summary": "Tek cümlelik hafıza özeti",
    "tags": "etiketler"
}}
"""
    # 1. Önce Gemini ile dene (Gemini kişisel verilerde reddetme yapmaz)
    if genai_client:
        models_to_try = ["gemini-3.8-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
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

    # 2. Gemini olmazsa Groq ile dene
    if groq_client:
        try:
            response = groq_client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            raw_content = response.choices[0].message.content
            return json.loads(raw_content)
        except Exception:
            pass

    # 3. Fallback: Eğer modeller JSON üretmeyi reddederse akıllı sezgisel yönlendirme
    lower_text = text.lower()
    if any(q in lower_text for q in ["neydi", "kaçtı", "nerede", "kimdi", "ne zaman", "hangisi", "?"]):
        return {"action": "query", "search_keywords": text, "question": text}
    elif any(r in lower_text for r in ["hatırlat", "alarm", "sonra", "yarın", "saat"]):
        return {"action": "reminder", "remind_at": current_time.strftime("%Y-%m-%d %H:%M:00"), "content": text}
    else:
        return {"action": "note", "summary": text, "tags": "genel"}

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
    # 1. Önce Gemini ile yanıt üret
    if genai_client:
        models_to_try = ["gemini-3.8-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
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

    # 2. Groq ile dene
    if groq_client:
        try:
            response = groq_client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Cevap üretilirken bir sorun oluştu: {e}"

    return "Yapay zeka servislerine erişilemedi."
