from __future__ import annotations

import json

import httpx

from app.config import get_settings


class OllamaService:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.settings.ollama_model,
            "prompt": f"{system_prompt}\n\n{user_prompt}",
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(f"{self.settings.ollama_base_url}/api/generate", json=payload)
                response.raise_for_status()
                data = response.json()
                return data.get("response", "").strip()
        except Exception:
            return self._fallback_response(user_prompt)

    def _fallback_response(self, user_prompt: str) -> str:
        niche = self._guess_niche(user_prompt)
        return json.dumps(
            {
                "title": self._extract_title(user_prompt),
                "niche": niche,
                "target_audience": "Clientes locales y visitantes digitales",
                "design_style": "moderno-editorial",
                "palette": {
                    "primary": "#14532d",
                    "secondary": "#f59e0b",
                    "background": "#f8fafc",
                    "text": "#0f172a",
                },
                "sections": self._sections_for_niche(niche),
                "seo_description": "Blog generado localmente con BlogBot IA",
            }
        )

    def _extract_title(self, prompt: str) -> str:
        clean = prompt.strip().replace('"', "")
        return clean[:70].capitalize() if clean else "Nuevo blog"

    def _guess_niche(self, prompt: str) -> str:
        lowered = prompt.lower()
        if any(word in lowered for word in ("mundial", "fifa", "copa", "partido", "fixture", "torneo", "seleccion", "futbol", "deporte")):
            return "deportes"
        if "turismo" in lowered:
            return "turismo"
        if "cafe" in lowered or "cafeter" in lowered:
            return "cafeteria"
        if "tecnolog" in lowered:
            return "tecnologia"
        if "noticia" in lowered or "news" in lowered or "actualidad" in lowered:
            return "noticias"
        if "medio ambiente" in lowered or "ambiental" in lowered or "sostenib" in lowered or "ecolog" in lowered:
            return "medio_ambiente"
        return "corporativo"

    def _sections_for_niche(self, niche: str) -> list[str]:
        if niche == "deportes":
            return ["hero", "agenda", "teams", "gallery", "faq", "contact"]
        if niche == "noticias":
            return ["hero", "coverage", "analysis", "gallery", "faq", "contact"]
        if niche == "medio_ambiente":
            return ["hero", "problem", "solutions", "gallery", "faq", "contact"]
        if niche == "tecnologia":
            return ["hero", "product", "analysis", "gallery", "faq", "contact"]
        return ["hero", "overview", "sections", "gallery", "faq", "contact"]
