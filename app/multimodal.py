import easyocr
import whisper
import os
from typing import Optional
import tempfile

# Initialize EasyOCR reader (run once)
# This will download the models on first run
ocr_reader = easyocr.Reader(['en'], gpu=False, verbose=False) 

# Initialize Whisper model (base is small and fast)
audio_model = whisper.load_model("base")

async def process_image(file_bytes) -> str:
    """Extract text from image using EasyOCR."""
    try:
        # Save bytes to a temporary file because EasyOCR prefers file paths
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        
        results = ocr_reader.readtext(tmp_path, detail=0)
        os.unlink(tmp_path)
        
        return " ".join(results)
    except Exception as e:
        print(f"OCR Error: {e}")
        return ""

async def process_audio(file_bytes) -> str:
    """Extract text from audio using Whisper."""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
            
        result = audio_model.transcribe(tmp_path)
        os.unlink(tmp_path)
        
        return result["text"].strip()
    except Exception as e:
        print(f"STT Error: {e}")
        return ""
