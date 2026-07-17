from __future__ import annotations

from html import escape, unescape
from urllib.parse import quote

import httpx

from app.config import get_settings


class MediaLibraryService:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def search_images(self, query: str, limit: int = 6, niche: str = "", topic: str = "") -> list[dict]:
        providers = [
            self._search_pexels,
            self._search_unsplash,
            self._search_openverse,
        ]
        query_bundle = self._build_query_bundle(query, niche, topic)
        images: list[dict] = []
        for provider in providers:
            try:
                images.extend(await provider(query_bundle, limit))
            except Exception:
                continue
            if len(images) >= limit:
                break

        ranked = self._rank_images(images, query_bundle)
        deduped: list[dict] = []
        seen = set()
        for image in ranked:
            key = image.get("page_url") or image.get("thumbnail_url")
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(image)
            if len(deduped) >= limit:
                break
        if len(deduped) < limit:
            deduped.extend(self._build_generated_visuals(query_bundle, limit - len(deduped)))
        return deduped

    async def _search_pexels(self, query_bundle: dict, limit: int) -> list[dict]:
        if not self.settings.pexels_api_key:
            return []
        params = {"query": query_bundle["primary"], "per_page": limit, "orientation": "landscape"}
        headers = {"Authorization": self.settings.pexels_api_key}
        async with httpx.AsyncClient(timeout=25.0, trust_env=False) as client:
            response = await client.get("https://api.pexels.com/v1/search", params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()

        images = []
        for photo in payload.get("photos", []):
            photographer = photo.get("photographer") or "Fotografo no especificado"
            page_url = photo.get("url") or ""
            images.append(
                {
                    "title": self._title_from_url(page_url, fallback="Imagen Pexels"),
                    "thumbnail_url": photo.get("src", {}).get("large") or photo.get("src", {}).get("medium") or "",
                    "page_url": page_url,
                    "author": photographer,
                    "author_url": photo.get("photographer_url") or "",
                    "license": "Pexels License",
                    "source": "Pexels",
                    "attribution_html": f'Foto por <a href="{photo.get("photographer_url") or page_url}" target="_blank" rel="noreferrer">{photographer}</a> en <a href="https://www.pexels.com" target="_blank" rel="noreferrer">Pexels</a>',
                }
            )
        return images

    async def _search_unsplash(self, query_bundle: dict, limit: int) -> list[dict]:
        if not self.settings.unsplash_access_key:
            return []
        params = {
            "query": query_bundle["primary"],
            "per_page": limit,
            "orientation": "landscape",
            "content_filter": "high",
        }
        headers = {"Authorization": f"Client-ID {self.settings.unsplash_access_key}"}
        async with httpx.AsyncClient(timeout=25.0, trust_env=False) as client:
            response = await client.get("https://api.unsplash.com/search/photos", params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()

        images = []
        for photo in payload.get("results", []):
            user = photo.get("user") or {}
            photographer = user.get("name") or "Autor no especificado"
            profile = (user.get("links") or {}).get("html") or photo.get("links", {}).get("html") or ""
            images.append(
                {
                    "title": photo.get("alt_description") or photo.get("description") or "Imagen Unsplash",
                    "thumbnail_url": (photo.get("urls") or {}).get("regular") or (photo.get("urls") or {}).get("small") or "",
                    "page_url": photo.get("links", {}).get("html") or "",
                    "author": photographer,
                    "author_url": profile,
                    "license": "Unsplash License",
                    "source": "Unsplash",
                    "attribution_html": f'Foto por <a href="{profile}" target="_blank" rel="noreferrer">{photographer}</a> en <a href="https://unsplash.com" target="_blank" rel="noreferrer">Unsplash</a>',
                }
            )
        return images

    async def _search_openverse(self, query_bundle: dict, limit: int) -> list[dict]:
        images = []
        async with httpx.AsyncClient(timeout=25.0, trust_env=False) as client:
            for candidate in query_bundle["queries"]:
                params = {
                    "q": candidate,
                    "page_size": max(limit * 3, 12),
                    "mature": "false",
                }
                response = await client.get(f"{self.settings.openverse_base_url}/v1/images/", params=params)
                response.raise_for_status()
                payload = response.json()
                for item in payload.get("results", []):
                    source = (item.get("source") or "openverse").title()
                    if "wikimedia" in source.lower() or "wiki" in source.lower():
                        continue
                    creator = item.get("creator") or "Autor no especificado"
                    license_name = item.get("license") or "Licencia abierta"
                    license_version = item.get("license_version") or ""
                    license_label = f"{license_name.upper()} {license_version}".strip()
                    page_url = item.get("foreign_landing_url") or item.get("url") or self._openverse_search_url(candidate)
                    creator_url = item.get("creator_url") or page_url
                    images.append(
                        {
                            "title": item.get("title") or "Imagen Openverse",
                            "thumbnail_url": item.get("thumbnail") or item.get("url") or self._fallback_thumbnail(item.get("title") or "Openverse"),
                            "page_url": page_url,
                            "author": creator,
                            "author_url": creator_url,
                            "license": license_label,
                            "source": source,
                            "attribution_html": f'Foto por <a href="{creator_url}" target="_blank" rel="noreferrer">{creator}</a> via <a href="{page_url}" target="_blank" rel="noreferrer">{source}</a> | {license_label}',
                            "usage_hint": "Imagen abierta encontrada en Openverse desde una fuente externa con atribucion visible.",
                            "best_time": "Ideal para inspirar una visita o complementar la seccion visual del destino.",
                            "price_hint": "Uso condicionado por la licencia mostrada en la fuente original.",
                            "duration_hint": "Consulta el contexto de la fuente para entender mejor el lugar representado.",
                            "tips": "Verifica la pagina original para confirmar autor, licencia y descripcion del lugar.",
                            "what_to_bring": "Si visitas el destino real, lleva agua, proteccion solar y bateria para fotos.",
                            "note": "La imagen se tomo de una fuente abierta indexada por Openverse y puede requerir atribucion al reutilizarla.",
                        }
                    )
                if len(images) >= max(limit * 2, 8):
                    break
        return images

    def _build_query_bundle(self, query: str, niche: str, topic: str) -> dict:
        base = " ".join(part for part in query.split() if part)
        base_lower = base.lower()
        topic_clean = " ".join(part for part in topic.split() if part)
        candidates = [base]
        if topic_clean and topic_clean.lower() not in base_lower:
            candidates.insert(0, topic_clean)
            candidates.insert(1, f"{topic_clean} {niche}".strip())
        if niche == "turismo" and "mocoa" in base_lower:
            candidates.extend(
                [
                    "mocoa putumayo colombia",
                    "putumayo colombia waterfall",
                    "amazonas colombia naturaleza",
                    "colombia ecotourism waterfall",
                ]
            )
        elif niche == "noticias":
            candidates.extend([f"{topic_clean} newsroom", f"{topic_clean} journalism", f"{topic_clean} headline"])
        elif niche == "medio_ambiente":
            candidates.extend([f"{topic_clean} environment", f"{topic_clean} sustainability", f"{topic_clean} nature"])
        elif niche == "deportes":
            candidates.extend([f"{topic_clean} sports", f"{topic_clean} stadium", f"{topic_clean} athlete"])
        elif niche == "tecnologia":
            candidates.extend([f"{topic_clean} technology", f"{topic_clean} innovation", f"{topic_clean} digital"])

        parts = [part for part in base.split() if len(part) > 3]
        if parts:
            candidates.append(" ".join(parts[:3]))
            candidates.append(" ".join(parts[-3:]))
        if niche == "turismo":
            candidates.append("colombia tourism nature")
        ordered = []
        seen = set()
        for candidate in candidates:
            normalized = candidate.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
        return {
            "primary": ordered[0] if ordered else base,
            "queries": ordered,
            "tokens": self._relevance_tokens(" ".join(ordered[:4])),
            "niche": niche,
            "topic": topic_clean,
        }

    def _rank_images(self, images: list[dict], query_bundle: dict) -> list[dict]:
        return sorted(images, key=lambda image: self._score_image(image, query_bundle), reverse=True)

    def _score_image(self, image: dict, query_bundle: dict) -> int:
        haystack = " ".join(
            [
                str(image.get("title", "")),
                str(image.get("source", "")),
                str(image.get("page_url", "")),
                str(image.get("author", "")),
            ]
        ).lower()
        score = 0
        for token in query_bundle["tokens"]:
            if token in haystack:
                score += 4 if len(token) > 5 else 2
        topic = (query_bundle.get("topic") or "").lower()
        if topic and topic in haystack:
            score += 10
        niche = query_bundle.get("niche", "")
        if niche == "noticias" and any(word in haystack for word in ("news", "press", "newspaper", "journalism")):
            score += 6
        if niche == "medio_ambiente" and any(word in haystack for word in ("nature", "forest", "climate", "environment", "sustain")):
            score += 6
        if niche == "deportes" and any(word in haystack for word in ("sport", "stadium", "soccer", "football", "basketball", "athlete")):
            score += 6
        if niche == "tecnologia" and any(word in haystack for word in ("technology", "digital", "computer", "innovation", "ai")):
            score += 6
        if niche == "turismo" and any(word in haystack for word in ("travel", "waterfall", "landscape", "tourism", "nature")):
            score += 6
        penalty_words = ("generic", "abstract", "template", "placeholder")
        if any(word in haystack for word in penalty_words):
            score -= 5
        return score

    def _relevance_tokens(self, text: str) -> list[str]:
        stopwords = {"blog", "sobre", "para", "con", "the", "and", "news", "image", "images", "editorial"}
        tokens = []
        for token in text.lower().replace(",", " ").replace(";", " ").split():
            token = token.strip()
            if len(token) < 4 or token in stopwords:
                continue
            tokens.append(token)
        unique = []
        seen = set()
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            unique.append(token)
        return unique

    def _title_from_url(self, url: str, fallback: str) -> str:
        if not url:
            return fallback
        tail = url.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ").strip()
        return tail.capitalize() if tail else fallback

    def _openverse_search_url(self, query: str) -> str:
        return f"https://openverse.org/search/image?q={quote(query)}"

    def _fallback_thumbnail(self, title: str) -> str:
        return f"https://placehold.co/900x600?text={quote(title[:40])}"

    def _build_generated_visuals(self, query_bundle: dict, missing: int) -> list[dict]:
        visuals = []
        topic = query_bundle.get("topic") or query_bundle.get("primary") or "Tema"
        niche = query_bundle.get("niche") or "general"
        palette_map = {
            "noticias": ("#991b1b", "#facc15", "#ffffff"),
            "medio_ambiente": ("#166534", "#84cc16", "#f7fee7"),
            "deportes": ("#1d4ed8", "#f97316", "#eff6ff"),
            "tecnologia": ("#0f172a", "#38bdf8", "#f8fafc"),
            "cafeteria": ("#78350f", "#f59e0b", "#fffbeb"),
        }
        primary, secondary, surface = palette_map.get(niche, ("#334155", "#f59e0b", "#f8fafc"))
        for index in range(missing):
            label = f"{topic[:42]} {index + 1}".strip()
            visuals.append(
                {
                    "title": f"Visual editorial: {label}",
                    "thumbnail_url": self._svg_data_uri(label, primary, secondary, surface),
                    "page_url": "",
                    "author": "BlogBot IA",
                    "author_url": "",
                    "license": "Visual generado localmente",
                    "source": "BlogBot IA",
                    "attribution_html": "Visual editorial generado localmente por BlogBot IA",
                    "usage_hint": "Visual de respaldo cuando no hay imagenes externas relevantes.",
                    "best_time": "Ideal para no dejar vacia la galeria mientras se curan imagenes finales.",
                    "price_hint": "No depende de derechos de terceros porque es un visual editorial local.",
                    "duration_hint": "Puedes reemplazarlo luego por una imagen externa o propia si prefieres.",
                    "tips": "Usalo como apoyo visual temporal o mantenlo como recurso grafico del blog.",
                    "what_to_bring": "No requiere atribucion a terceros.",
                    "note": "Imagen de respaldo generada localmente para mantener coherencia visual con el tema.",
                }
            )
        return visuals

    def _svg_data_uri(self, label: str, primary: str, secondary: str, surface: str) -> str:
        safe_label = escape(label)
        svg = f"""
<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1200 800' role='img' aria-label='{safe_label}'>
  <defs>
    <linearGradient id='bg' x1='0' y1='0' x2='1' y2='1'>
      <stop offset='0%' stop-color='{primary}' />
      <stop offset='100%' stop-color='{secondary}' />
    </linearGradient>
  </defs>
  <rect width='1200' height='800' fill='{surface}' />
  <rect x='32' y='32' width='1136' height='736' rx='42' fill='url(#bg)' opacity='0.95' />
  <circle cx='190' cy='180' r='96' fill='{surface}' opacity='0.12' />
  <circle cx='1000' cy='620' r='132' fill='{surface}' opacity='0.12' />
  <rect x='110' y='150' width='980' height='500' rx='28' fill='{surface}' opacity='0.13' />
  <text x='120' y='250' fill='white' font-size='34' font-family='Segoe UI, Arial, sans-serif' letter-spacing='5'>BLOGBOT IA</text>
  <text x='120' y='360' fill='white' font-size='78' font-weight='700' font-family='Segoe UI, Arial, sans-serif'>{safe_label[:28]}</text>
  <text x='120' y='430' fill='white' font-size='78' font-weight='700' font-family='Segoe UI, Arial, sans-serif'>{safe_label[28:56]}</text>
  <text x='120' y='560' fill='white' font-size='28' font-family='Segoe UI, Arial, sans-serif' opacity='0.92'>Visual editorial generado localmente para este tema</text>
</svg>
        """.strip()
        return f"data:image/svg+xml;utf8,{quote(svg)}"
