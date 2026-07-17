from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.schemas import VoiceSynthesisRequest
from app.services.speech_service import SpeechService


router = APIRouter(prefix="/api/voice", tags=["voice"])
speech_service = SpeechService()


@router.get("/status")
def voice_status():
    return {
        "stt": speech_service.stt_status(),
        "tts": speech_service.tts_status(),
    }


@router.post("/stt")
async def speech_to_text(file: UploadFile = File(...)):
    suffix = Path(file.filename or "audio.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
        temp.write(await file.read())
        temp_path = Path(temp.name)
    try:
        result = await speech_service.transcribe_file(temp_path)
        if not result.get("available"):
            raise HTTPException(status_code=503, detail=result.get("reason") or "STT no disponible")
        return result
    finally:
        temp_path.unlink(missing_ok=True)


@router.post("/tts")
async def text_to_speech(payload: VoiceSynthesisRequest):
    audio_bytes, status = await speech_service.synthesize_bytes(payload.text)
    if not audio_bytes:
        raise HTTPException(status_code=503, detail=status.get("reason") or "TTS no disponible")
    return StreamingResponse(iter([audio_bytes]), media_type="audio/wav")
