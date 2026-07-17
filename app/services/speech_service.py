from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path

from app.config import get_settings


class SpeechService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._whisper_model = None
        self._whisper_error: str | None = None
        self._kokoro_pipeline = None
        self._kokoro_error: str | None = None

    def stt_status(self) -> dict:
        model = self._get_whisper_model()
        return {"available": model is not None, "reason": self._whisper_error}

    def tts_status(self) -> dict:
        pipeline = self._get_kokoro_pipeline()
        return {"available": pipeline is not None, "reason": self._kokoro_error}

    async def transcribe_file(self, audio_path: Path) -> dict:
        model = self._get_whisper_model()
        if model is None:
            return {"available": False, "reason": self._whisper_error or "STT no disponible", "text": ""}

        def _run() -> dict:
            segments, info = model.transcribe(
                str(audio_path),
                language=self.settings.stt_language or None,
                beam_size=5,
                vad_filter=True,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
            return {
                "available": True,
                "reason": None,
                "text": text,
                "language": getattr(info, "language", None),
                "language_probability": getattr(info, "language_probability", None),
            }

        try:
            return await asyncio.to_thread(_run)
        except Exception as exc:
            return {"available": False, "reason": f"Error transcribiendo audio: {exc}", "text": ""}

    async def synthesize_bytes(self, text: str) -> tuple[bytes | None, dict]:
        pipeline = self._get_kokoro_pipeline()
        if pipeline is None:
            return None, {"available": False, "reason": self._kokoro_error or "TTS no disponible"}

        def _run() -> bytes:
            import numpy as np
            import soundfile as sf

            chunks: list[np.ndarray] = []
            generator = pipeline(text, voice=self.settings.kokoro_voice)
            for _, _, audio in generator:
                chunks.append(np.asarray(audio, dtype=np.float32))
            if not chunks:
                raise RuntimeError("Kokoro no devolvio audio")
            merged = np.concatenate(chunks)
            buffer = io.BytesIO()
            sf.write(buffer, merged, 24000, format="WAV")
            return buffer.getvalue()

        try:
            audio_bytes = await asyncio.to_thread(_run)
            return audio_bytes, {"available": True, "reason": None}
        except Exception as exc:
            return None, {"available": False, "reason": f"Error generando audio: {exc}"}

    def _get_whisper_model(self):
        if self._whisper_model is not None:
            return self._whisper_model
        if self._whisper_error is not None:
            return None
        try:
            self._clear_proxy_env()
            from faster_whisper import WhisperModel

            self._whisper_model = WhisperModel(
                self.settings.whisper_model,
                device=self.settings.whisper_device,
                compute_type=self.settings.whisper_compute_type,
                cpu_threads=self.settings.whisper_cpu_threads,
            )
            return self._whisper_model
        except Exception as exc:
            self._whisper_error = str(exc)
            return None

    def _get_kokoro_pipeline(self):
        if self._kokoro_pipeline is not None:
            return self._kokoro_pipeline
        if self._kokoro_error is not None:
            return None
        try:
            self._clear_proxy_env()
            from kokoro import KPipeline

            self._kokoro_pipeline = KPipeline(lang_code=self.settings.kokoro_lang_code, repo_id="hexgrad/Kokoro-82M")
            return self._kokoro_pipeline
        except Exception as exc:
            self._kokoro_error = str(exc)
            return None

    def _clear_proxy_env(self) -> None:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            os.environ.pop(key, None)
        cache_root = str(self.settings.model_cache_path)
        os.environ["HF_HOME"] = cache_root
        os.environ["HUGGINGFACE_HUB_CACHE"] = cache_root
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
