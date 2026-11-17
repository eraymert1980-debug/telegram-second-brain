import os
import json
import base64
from datetime import datetime
from groq import Groq

# Groq Client
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

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
    
    with open(audio_path, "rb") as file:
        transcription = groq_client.audio.transcriptions.create(
            file=(os.path.basename(audio_path), file.read()),
            model="whisper-large-v3-turbo",
            language="tr",
            response_format="text"
        )
    return str(transcription).strip()

def analyze_image(image_path: str, caption: str = "") -> str:
    """Görseli analiz eder (Gemini Client varsa Gemini 2.5 Flash ile)."""
    if genai_client:
        prompt = (
            "Bu görseli bir kişisel hafıza ve not alma asistanı olarak incele. "
            "Görseldeki önemli nesneleri, eşyaların yerlerini, yazıları/kart numaralarını veya belgeleri "
            "ayrıntılı, net ve aranabilir bir Türkçe ile açıkla. "
            "Örnek: 'Mavi anahtarlık antredeki konsolun sol çekmecesine konulmuş.' "
            "veya 'KuveytTürk hesap cüzdanı: Müşteri no: 123456, IBAN: TR...'"
        )
        if caption:
            prompt += f"\nKullanıcının görselle birlikte ilettiği not: '{caption}'"

        with open(image_path, "rb") as img_file:
            image_bytes = img_file.read()

        response = genai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                genai.types.Part.from_bytes(data=image_bytes, mime_type='image/jpeg'),
                prompt
            ]
        )
        return response.text.strip()
    else:
        # Eğer henüz Gemini API key tanımlanmadıysa fallback
        fallback_desc = "Fotoğraf kaydedildi."
        if caption:
            fallback_desc += f" Kullanıcı notu: {caption}"
        return fallback_desc

def classify_and_parse(text: str, current_time: datetime = None) -> dict:
    """
    Kullanıcının yazdığı veya sesle söylediği cümleyi inceler:
    1. 'reminder': Eğer zaman belirtip bir şey hatırlatılmasını istiyorsa.
    2. 'query': Eğer geçmiş bir notu/bilgiyi soruyorsa.
    3. 'note': Yeni bir bilgi/not kaydediyorsa.
    """
    if not groq_client:
        raise ValueError("GROQ_API_KEY ayarlanmamış!")
        
    if current_time is None:
        current_time = datetime.now()
        
    current_time_str = current_time.strftime("%Y-%m-%d %H:%M:%S (%A)")
    
    system_prompt = f"""
Sen kullanıcının kişisel yapay zeka asistanısın.
Şu anki gerçek zaman: {current_time_str}.

Kullanıcıdan gelen mesajı analiz et ve SADECE geçerli bir JSON objesi döndür (başka hiçbir metin veya markdown formatı ekleme):

1. Eğer kullanıcı gelecekte bir şeyi hatırlatmanı istiyorsa (Örn: "Salı günü saat 14:00'te vergi dairesini aramayı hatırlat", "yarın sabah kuveyttürk borcunu hatırlat", "10 dakika sonra kahveyi al"):
{{
    "action": "reminder",
    "remind_at": "YYYY-MM-DD HH:MM:00",
    "content": "Hatırlatılacak görev özeti"
}}

2. Eğer kullanıcı geçmişe dair bir soru soruyorsa (Örn: "Anahtarı nereye koymuştum?", "KuveytTürk müşteri numaram kaçtı?", "Şunu nereye saklamıştım?"):
{{
    "action": "query",
    "search_keywords": "veritabanında aranacak anahtar kelimeler",
    "question": "kullanıcının orijinal sorusu"
}}

3. Eğer kullanıcı yeni bir bilgi, not veya durum kaydediyorsa (Örn: "KuveytTürk müşteri numaram 987654", "Yedek anahtarı vestiyere koydum", "İlaçlarımı mutfak dolabına kaldırdım"):
{{
    "action": "note",
    "summary": "Tek cümlelik net hafıza özeti",
    "tags": "virgülle ayrılmış etiketler"
}}
"""
    
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

def answer_query(user_question: str, notes: list) -> str:
    """Bulunan notlara göre kullanıcıya doğal, net ve samimi bir Türkçe yanıt üretir."""
    if not groq_client:
        raise ValueError("GROQ_API_KEY ayarlanmamış!")
        
    context_text = ""
    if notes:
        for idx, n in enumerate(notes, 1):
            context_text += f"{idx}. [Tarih: {n['created_at']}, Tip: {n['msg_type']}] {n['content']}\n"
    else:
        context_text = "Hafızada bu konuyla ilgili doğrudan bir kayıt bulunamadı."
        
    prompt = f"""
Kullanıcı sana geçmişe dair bir soru sordu:
Soru: "{user_question}"

Kullanıcının kişisel hafıza veritabanından bulunan ilgili notlar:
{context_text}

GÖREVİN:
1. Eğer hafızada kullanıcının aradığı bilgi varsa doğrudan, net ve samimi bir dille cevap ver (Örn: "KuveytTürk şahsi müşteri numaran: 12345678 (14 Ekim'de kaydetmiştin)").
2. Eğer notlarda tam cevap yoksa ama benzer bir bilgi varsa bunu nazikçe belirt.
3. Kesinlikle uydurma yapma, yalnızca notlardaki bilgilere sadık kal.
"""
    
    response = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=0.2,
    )
    return response.choices[0].message.content.strip()
