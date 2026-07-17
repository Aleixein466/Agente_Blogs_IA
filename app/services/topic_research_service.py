from __future__ import annotations

import asyncio
import re
from html import unescape
from urllib.parse import quote

import httpx


class TopicResearchService:
    def __init__(self) -> None:
        self.wikipedia_api = "https://es.wikipedia.org/w/api.php"
        self.wikipedia_rest = "https://es.wikipedia.org/api/rest_v1/page/summary"
        self.openalex_api = "https://api.openalex.org/works"

    async def research(self, topic: str, niche: str, prompt: str = "") -> dict:
        topic = self._clean_text(topic) or "Tema principal"
        focus_phrase = self._focus_phrase(prompt, topic)
        wikipedia_hits = await self._search_wikipedia(focus_phrase or topic)
        wikipedia_summaries = await self._fetch_hit_summaries(wikipedia_hits[:3])
        summary = wikipedia_summaries[0] if wikipedia_summaries else {}
        scholarly_refs = await self._search_openalex(focus_phrase or topic)

        research = {
            "topic": topic,
            "focus_phrase": focus_phrase or topic,
            "summary": self._build_summary(topic, niche, summary, wikipedia_hits),
            "key_points": self._build_key_points(topic, niche, summary, wikipedia_hits, scholarly_refs),
            "references": self._build_references(summary, wikipedia_hits, wikipedia_summaries, scholarly_refs),
            "keywords": self._build_keywords(topic, niche, summary, wikipedia_hits, scholarly_refs),
            "image_query": self._build_image_query(topic, niche, summary, wikipedia_hits, focus_phrase),
            "citation_note": self._citation_note(summary, scholarly_refs),
        }
        if not research["references"]:
            research["references"] = self._fallback_references(topic, niche)
        if not research["key_points"]:
            research["key_points"] = self._fallback_points(topic, niche)
        return research

    async def _search_wikipedia(self, topic: str) -> list[dict]:
        params = {
            "action": "query",
            "list": "search",
            "srsearch": topic,
            "utf8": "1",
            "format": "json",
            "srlimit": 4,
        }
        try:
            async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
                response = await client.get(self.wikipedia_api, params=params)
                response.raise_for_status()
                payload = response.json()
        except Exception:
            return []

        hits = []
        for item in payload.get("query", {}).get("search", []):
            title = self._clean_text(item.get("title", ""))
            snippet = self._strip_html(item.get("snippet", ""))
            if not title:
                continue
            hits.append(
                {
                    "title": title,
                    "snippet": snippet,
                    "url": f"https://es.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                    "source": "Wikipedia",
                }
            )
        return hits

    async def _fetch_summary(self, hit: dict) -> dict:
        title = hit.get("title", "")
        if not title:
            return {}
        try:
            async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
                response = await client.get(f"{self.wikipedia_rest}/{quote(title.replace(' ', '_'))}")
                response.raise_for_status()
                payload = response.json()
        except Exception:
            return {}
        return {
            "title": self._clean_text(payload.get("title", title)),
            "extract": self._clean_text(payload.get("extract", "")),
            "url": payload.get("content_urls", {}).get("desktop", {}).get("page") or hit.get("url", ""),
            "thumbnail": payload.get("thumbnail", {}).get("source", ""),
            "description": self._clean_text(payload.get("description", "")),
            "source": "Wikipedia",
        }

    async def _fetch_hit_summaries(self, hits: list[dict]) -> list[dict]:
        if not hits:
            return []
        results = await asyncio.gather(*(self._fetch_summary(hit) for hit in hits), return_exceptions=True)
        summaries = []
        for result in results:
            if isinstance(result, Exception) or not result:
                continue
            summaries.append(result)
        return summaries

    async def _search_openalex(self, topic: str) -> list[dict]:
        params = {
            "search": topic,
            "per-page": 4,
            "sort": "relevance_score:desc",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
                response = await client.get(self.openalex_api, params=params)
                response.raise_for_status()
                payload = response.json()
        except Exception:
            return []

        works = []
        for item in payload.get("results", []):
            title = self._clean_text(item.get("display_name", ""))
            if not title:
                continue
            source = item.get("primary_location", {}).get("source", {}) or {}
            works.append(
                {
                    "title": title,
                    "snippet": self._abstract_from_inverted_index(item.get("abstract_inverted_index") or {}),
                    "url": item.get("primary_location", {}).get("landing_page_url")
                    or item.get("primary_location", {}).get("pdf_url")
                    or item.get("id", ""),
                    "source": self._clean_text(source.get("display_name", "OpenAlex")),
                    "year": item.get("publication_year"),
                    "authors": self._author_names(item.get("authorships", [])),
                }
            )
        return works

    def _build_summary(self, topic: str, niche: str, summary: dict, wikipedia_hits: list[dict]) -> str:
        if summary.get("extract"):
            return summary["extract"]
        if wikipedia_hits:
            return wikipedia_hits[0].get("snippet", "")
        return self._fallback_summary(topic, niche)

    def _build_key_points(
        self,
        topic: str,
        niche: str,
        summary: dict,
        wikipedia_hits: list[dict],
        scholarly_refs: list[dict],
    ) -> list[dict]:
        points: list[dict] = []
        if summary.get("extract"):
            for sentence in self._sentences(summary["extract"])[:2]:
                points.append({"title": self._title_from_sentence(sentence), "description": sentence, "source": "Wikipedia"})
        for hit in wikipedia_hits[1:3]:
            snippet = hit.get("snippet", "")
            if snippet:
                points.append({"title": hit["title"], "description": snippet, "source": hit["source"]})
        for ref in scholarly_refs[:2]:
            snippet = ref.get("snippet") or f"Lectura complementaria desde {ref.get('source', 'OpenAlex')}."
            points.append({"title": ref["title"], "description": snippet[:260], "source": ref.get("source", "OpenAlex")})
        unique: list[dict] = []
        seen = set()
        for point in points:
            key = point["title"].lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(point)
            if len(unique) >= 5:
                break
        return unique or self._fallback_points(topic, niche)

    def _build_references(
        self,
        summary: dict,
        wikipedia_hits: list[dict],
        wikipedia_summaries: list[dict],
        scholarly_refs: list[dict],
    ) -> list[dict]:
        refs: list[dict] = []
        if summary.get("url"):
            refs.append(
                {
                    "title": summary.get("title", "Resumen tematico"),
                    "url": summary["url"],
                    "source": summary.get("source", "Wikipedia"),
                    "snippet": summary.get("extract", ""),
                    "label": "Contexto general",
                    "image_url": summary.get("thumbnail", ""),
                    "official_label": "Ir a la fuente",
                }
            )
        for item in wikipedia_summaries[1:3]:
            refs.append(
                {
                    "title": item.get("title", "Articulo relacionado"),
                    "url": item.get("url", ""),
                    "source": item.get("source", "Wikipedia"),
                    "snippet": item.get("extract", "") or item.get("description", ""),
                    "label": "Articulo de apoyo",
                    "image_url": item.get("thumbnail", ""),
                    "official_label": "Abrir articulo",
                }
            )
        if not wikipedia_summaries:
            for hit in wikipedia_hits[1:3]:
                refs.append(
                    {
                        "title": hit["title"],
                        "url": hit.get("url", ""),
                        "source": hit.get("source", "Wikipedia"),
                        "snippet": hit.get("snippet", ""),
                        "label": "Articulo de apoyo",
                        "image_url": "",
                        "official_label": "Abrir articulo",
                    }
                )
        for ref in scholarly_refs[:4]:
            meta_bits = [bit for bit in [ref.get("authors"), str(ref.get("year") or "").strip()] if bit]
            refs.append(
                {
                    "title": ref["title"],
                    "url": ref.get("url", ""),
                    "source": ref.get("source", "OpenAlex"),
                    "snippet": ref.get("snippet", ""),
                    "label": "Referencia academica" + (f" | {' - '.join(meta_bits)}" if meta_bits else ""),
                    "image_url": "",
                    "official_label": "Abrir referencia",
                }
            )
        return refs[:6]

    def _build_keywords(
        self,
        topic: str,
        niche: str,
        summary: dict,
        wikipedia_hits: list[dict],
        scholarly_refs: list[dict],
    ) -> list[str]:
        candidates = [topic, niche.replace("_", " "), summary.get("description", "")]
        candidates.extend(hit.get("title", "") for hit in wikipedia_hits[:3])
        candidates.extend(ref.get("title", "") for ref in scholarly_refs[:2])
        tokens: list[str] = []
        for text in candidates:
            tokens.extend(self._tokenize(text))
        unique: list[str] = []
        seen = set()
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            unique.append(token)
            if len(unique) >= 8:
                break
        return unique

    def _build_image_query(self, topic: str, niche: str, summary: dict, wikipedia_hits: list[dict], focus_phrase: str = "") -> str:
        pieces = [focus_phrase or topic]
        if niche == "medio_ambiente":
            pieces.extend(["environment", "sustainability", "community"])
        elif niche == "noticias":
            pieces.extend(["journalism", "editorial", "press"])
        elif niche == "deportes":
            pieces.extend(["sport", "competition", "stadium"])
        elif niche == "tecnologia":
            pieces.extend(["technology", "innovation", "digital"])
        elif niche == "cafeteria":
            pieces.extend(["coffee", "cafe", "specialty"])
        else:
            pieces.extend(self._tokenize(summary.get("description", ""))[:3])
        if wikipedia_hits:
            pieces.extend(self._tokenize(wikipedia_hits[0].get("title", ""))[:2])
        return " ".join(piece for piece in pieces if piece).strip()

    def _focus_phrase(self, prompt: str, topic: str) -> str:
        clean = self._clean_text(prompt)
        if not clean:
            return topic
        lowered = clean.lower()
        prefixes = [
            "crea un blog sobre ",
            "crear un blog sobre ",
            "haz un blog sobre ",
            "hazme un blog sobre ",
            "blog sobre ",
            "blog de ",
        ]
        for prefix in prefixes:
            if lowered.startswith(prefix):
                clean = clean[len(prefix):]
                break
        fillers = [
            "que tenga", "que incluya", "incluye", "agrega", "con imagenes", "con imágenes",
            "con referencias", "con articulos", "con artículos", "para mi negocio", "para mi marca",
        ]
        for filler in fillers:
            idx = clean.lower().find(filler)
            if idx > 0:
                clean = clean[:idx].strip(" ,.;:")
        return self._clean_text(clean) or topic

    def _citation_note(self, summary: dict, scholarly_refs: list[dict]) -> str:
        sources = []
        if summary.get("url"):
            sources.append("Wikipedia")
        if scholarly_refs:
            sources.append("OpenAlex")
        if not sources:
            return "Contenido base generado sin conexion externa; conviene revisar datos y enriquecer con fuentes adicionales."
        return f"Base editorial construida con apoyo de {', '.join(sources)} y ajustada para una lectura web clara."

    def _fallback_summary(self, topic: str, niche: str) -> str:
        if niche == "medio_ambiente":
            return f"{topic} puede abordarse desde contexto, impacto social, datos de cambio y acciones concretas para comunidad y territorio."
        if niche == "noticias":
            return f"{topic} requiere contexto, cronologia, protagonistas y claves para que el lector entienda por que importa."
        if niche == "deportes":
            return f"{topic} se beneficia de una mezcla entre panorama general, calendario, protagonistas y puntos de tension competitiva."
        return f"{topic} puede convertirse en un blog completo si se organiza con contexto, hallazgos, recursos y lecturas recomendadas."

    def _fallback_points(self, topic: str, niche: str) -> list[dict]:
        mapping = {
            "medio_ambiente": [
                {"title": "Contexto del problema", "description": f"Explica que esta pasando alrededor de {topic} y por que afecta a la vida urbana.", "source": "Base editorial"},
                {"title": "Impactos visibles", "description": "Aterriza consecuencias en salud, espacio publico, habitos o gestion de residuos.", "source": "Base editorial"},
                {"title": "Acciones y soluciones", "description": "Incluye practicas, proyectos o politicas que ayuden a pasar del diagnostico a la accion.", "source": "Base editorial"},
            ],
            "noticias": [
                {"title": "Panorama general", "description": f"Resume el tema {topic} con foco en hechos, actores y relevancia publica.", "source": "Base editorial"},
                {"title": "Claves para entenderlo", "description": "Convierte el tema en lectura util con contexto, cronologia y seguimiento.", "source": "Base editorial"},
                {"title": "Lo que sigue", "description": "Cierra con escenarios, preguntas abiertas y temas para continuar la cobertura.", "source": "Base editorial"},
            ],
            "deportes": [
                {"title": "Panorama del evento", "description": f"Presenta {topic} con protagonistas, calendario y puntos de expectativa.", "source": "Base editorial"},
                {"title": "Ritmo competitivo", "description": "Desarrolla cruces, momentos clave o focos de analisis para mantener interes.", "source": "Base editorial"},
                {"title": "Historias y seguimiento", "description": "Abre espacio a perfiles, estadisticas y nuevas piezas derivadas.", "source": "Base editorial"},
            ],
        }
        return mapping.get(
            niche,
            [
                {"title": "Tema central", "description": f"Explica {topic} desde una promesa editorial clara.", "source": "Base editorial"},
                {"title": "Hallazgos y angulos", "description": "Convierte el tema en bloques utiles para lectura escaneable.", "source": "Base editorial"},
                {"title": "Recursos y siguiente paso", "description": "Cierra con referencias, herramientas o una accion concreta.", "source": "Base editorial"},
            ],
        )

    def _fallback_references(self, topic: str, niche: str) -> list[dict]:
        return [
            {
                "title": f"Guia base para profundizar en {topic}",
                "url": "",
                "source": "Base editorial",
                "snippet": self._fallback_summary(topic, niche),
                "label": "Referencia inicial",
                "image_url": "",
                "official_label": "Sin fuente externa",
            }
        ]

    def _abstract_from_inverted_index(self, inverted_index: dict) -> str:
        if not inverted_index:
            return ""
        words = sorted(((position, word) for word, positions in inverted_index.items() for position in positions), key=lambda item: item[0])
        abstract = " ".join(word for _, word in words)
        return self._clean_text(abstract[:420])

    def _author_names(self, authorships: list[dict]) -> str:
        names = []
        for authorship in authorships[:3]:
            name = self._clean_text((authorship.get("author") or {}).get("display_name", ""))
            if name:
                names.append(name)
        return ", ".join(names)

    def _title_from_sentence(self, sentence: str) -> str:
        cleaned = self._clean_text(sentence)
        if not cleaned:
            return "Punto clave"
        return cleaned[:72].rsplit(" ", 1)[0] if len(cleaned) > 72 else cleaned

    def _sentences(self, text: str) -> list[str]:
        return [chunk.strip() for chunk in re.split(r"(?<=[.!?])\s+", self._clean_text(text)) if chunk.strip()]

    def _strip_html(self, text: str) -> str:
        return self._clean_text(re.sub(r"<[^>]+>", " ", unescape(text or "")))

    def _tokenize(self, text: str) -> list[str]:
        stopwords = {
            "sobre", "para", "desde", "entre", "donde", "the", "with", "from", "this", "that",
            "tema", "blog", "del", "las", "los", "una", "unos", "unas", "como", "porque",
        }
        tokens = []
        for raw in re.split(r"[^a-zA-Z0-9áéíóúÁÉÍÓÚñÑ]+", self._clean_text(text).lower()):
            if len(raw) < 4 or raw in stopwords:
                continue
            tokens.append(raw)
        return tokens

    def _clean_text(self, text: str) -> str:
        return " ".join(unescape(str(text or "")).replace("\n", " ").split())
