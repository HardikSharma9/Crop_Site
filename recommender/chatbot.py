import base64
import io
import os
import urllib.request
from google import genai
from gtts import gTTS
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt

# Initialize Gemini Client using official SDK
api_key = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None

# Active production models with fallback mechanism
PRIMARY_MODEL = "gemini-1.5-flash"
FALLBACK_MODEL = "gemini-1.5-pro"

CROP_IMAGE_MAP = {
    "sugarcane": "https://images.pexels.com/photos/3025215/pexels-photo-3025215.jpeg?auto=compress&cs=tinysrgb&w=800",
    "maize": "https://images.pexels.com/photos/547263/pexels-photo-547263.jpeg?auto=compress&cs=tinysrgb&w=800",
    "corn": "https://images.pexels.com/photos/547263/pexels-photo-547263.jpeg?auto=compress&cs=tinysrgb&w=800",
    "pulses": "https://images.pexels.com/photos/7456720/pexels-photo-7456720.jpeg?auto=compress&cs=tinysrgb&w=800",
    "rice": "https://images.pexels.com/photos/247599/pexels-photo-247599.jpeg?auto=compress&cs=tinysrgb&w=800",
    "wheat": "https://images.pexels.com/photos/265216/pexels-photo-265216.jpeg?auto=compress&cs=tinysrgb&w=800",
    "cotton": "https://images.pexels.com/photos/6045710/pexels-photo-6045710.jpeg?auto=compress&cs=tinysrgb&w=800",
}


def is_image_request(message):
    image_keywords = ["picture", "photo", "image", "show", "look like", "see"]
    return any(kw in str(message).lower() for kw in image_keywords)


def fetch_image_as_base64(url):
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return base64.b64encode(resp.read()).decode("utf-8")
    except Exception as e:
        print(f"Image fetch error: {e}")
        return None


def generate_farm_image(query, language="auto"):
    query_lower = str(query).lower()
    for crop, img_url in CROP_IMAGE_MAP.items():
        if crop in query_lower:
            b64_data = fetch_image_as_base64(img_url)
            if b64_data:
                return b64_data

    fallback_url = "https://images.pexels.com/photos/2132250/pexels-photo-2132250.jpeg?auto=compress&cs=tinysrgb&w=800"
    return fetch_image_as_base64(fallback_url) or ""


def transcribe_audio(audio_bytes, mime_type="audio/webm", language="auto"):
    """Transcribes audio bytes to text using Gemini."""
    if not client:
        return "Audio transcription unavailable: GEMINI_API_KEY is not configured."

    for model_name in [PRIMARY_MODEL, FALLBACK_MODEL]:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[
                    f"Transcribe this spoken audio accurately verbatim. Context language: {language}.",
                    {"mime_type": mime_type, "data": audio_bytes},
                ],
            )
            return response.text.strip() if response.text else ""
        except Exception as e:
            print(f"Transcription error on model {model_name}: {e}")
            continue

    raise RuntimeError("Audio transcription failed across all models.")


def text_to_speech_base64(text, language="en"):
    """Converts input text to base64 audio stream for browser playback."""
    try:
        clean_text = text.replace("*", "").replace("#", "").replace("`", "")
        lang_code = language.split("-")[0].lower() if language else "en"
        tts = gTTS(text=clean_text[:500], lang=lang_code, slow=False)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)
        return base64.b64encode(fp.read()).decode("utf-8")
    except Exception as e:
        print(f"TTS Error: {e}")
        return ""


def get_chat_stream(msg, context="", language="auto"):
    if not client:
        yield f"Here is the agricultural advisory for: **{msg}**\n\nEnsure proper soil testing, crop rotation, and balanced fertilizer usage for best yield."
        return

    prompt = (
        f"You are FarmAI, an expert agricultural advisory assistant.\n"
        f"Language Context: {language}\n"
        f"Page Context: {context}\n"
        f"User Question: {msg}"
    )

    success = False
    for model_name in [PRIMARY_MODEL, FALLBACK_MODEL]:
        try:
            response = client.models.generate_content_stream(
                model=model_name,
                contents=prompt,
            )
            has_text = False
            for chunk in response:
                if chunk.text:
                    has_text = True
                    yield chunk.text

            if has_text:
                success = True
                break
        except Exception as e:
            print(f"Stream error on {model_name}: {e}")
            continue

    if not success:
        yield "\n[AI Error: Failed to generate response from Gemini API. Please verify your API key and quota.]"


@csrf_exempt
def chatbot_view(request):
    if request.method == "POST":
        message = request.POST.get("message", "").strip()
        language = request.POST.get("language", "auto")
        context = request.POST.get("context", "")

        if is_image_request(message):
            b64_img = generate_farm_image(message, language)
            if b64_img:
                return JsonResponse(
                    {
                        "type": "image",
                        "image": b64_img,
                        "mime_type": "image/png",
                        "message": "Here is your requested crop image.",
                    }
                )

        return StreamingHttpResponse(
            get_chat_stream(message, context, language),
            content_type="text/plain; charset=utf-8",
        )
    return JsonResponse({"error": "Invalid request method"}, status=400)