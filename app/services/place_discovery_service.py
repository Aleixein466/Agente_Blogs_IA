from __future__ import annotations

import httpx


class PlaceDiscoveryService:
    def __init__(self) -> None:
        self.headers = {"User-Agent": "BlogBotIA/1.0 (contact: local tourism discovery)"}

    async def discover_places(self, niche: str, location: str, limit: int = 6) -> list[dict]:
        if niche != "turismo":
            return []

        places = await self._search_osm_tourism_places(location, limit=limit)
        if places:
            return places

        return self._curated_fallback(location)[:limit]

    async def _search_osm_tourism_places(self, location: str, limit: int = 6) -> list[dict]:
        queries = self._candidate_queries(location)
        found: list[dict] = []
        seen = set()
        async with httpx.AsyncClient(timeout=20.0, trust_env=False, headers=self.headers) as client:
            for query in queries:
                try:
                    response = await client.get(
                        "https://nominatim.openstreetmap.org/search",
                        params={"q": query, "format": "jsonv2", "limit": 3},
                    )
                    response.raise_for_status()
                    results = response.json()
                except Exception:
                    continue

                for item in results:
                    name = (item.get("name") or "").strip()
                    display_name = item.get("display_name") or ""
                    if not name:
                        continue
                    normalized_title = self._normalize_title(name, display_name, location).lower()
                    key = normalized_title
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append(self._map_result_to_place(item, location))
                    if len(found) >= limit:
                        return found
        return found

    def _candidate_queries(self, location: str) -> list[str]:
        lowered = location.lower()
        if "mocoa" in lowered:
            return [
                f"Fin del Mundo {location}",
                f"Hornoyaco {location}",
                f"Ojo de Dios {location}",
                f"salto del indio {location}",
                f"cascada {location}",
                f"mirador {location}",
            ]
        return [
            f"tourist attraction {location}",
            f"waterfall {location}",
            f"mirador {location}",
            f"parque natural {location}",
            f"landmark {location}",
        ]

    def _map_result_to_place(self, item: dict, location: str) -> dict:
        raw_name = (item.get("name") or "Sitio turistico").strip()
        display_name = item.get("display_name") or raw_name
        item_type = item.get("type") or "tourism"
        category = item.get("category") or "tourism"
        title = self._normalize_title(raw_name, display_name, location)
        description = self._build_description(title, item_type, category, display_name, location)
        return {
            "tag": self._tag_for_type(item_type, category),
            "title": title,
            "description": description,
            "schedule": "Horario referencial: 8:00 a. m. - 4:30 p. m. si es ingreso natural o guiado; confirma antes de ir.",
            "price": "Precio referencial: validar entrada, guia, parqueadero o transporte segun operador.",
            "duration": "Tiempo recomendado: 1 a 4 horas segun acceso, clima y complejidad del recorrido.",
            "tips": "Consulta estado de la via, lluvia y apoyo de guias locales antes de salir.",
            "what_to_bring": "Agua, ropa comoda, zapatos con agarre, impermeable ligero y celular con bateria.",
            "note": f"Nombre detectado desde OpenStreetMap/Nominatim para la zona de {location}; confirma detalles operativos localmente.",
        }

    def _normalize_title(self, raw_name: str, display_name: str, location: str) -> str:
        lowered = raw_name.lower()
        if raw_name == "ojo" and "mocoa" in location.lower():
            return "Ojo de Dios"
        if "fin del mundo" in lowered:
            return "Fin del Mundo"
        if "hornoyaco" in lowered:
            return "Cascada Hornoyaco"
        if "salto del indio" in lowered:
            return "Salto del Indio"
        return raw_name.title()

    def _build_description(self, title: str, item_type: str, category: str, display_name: str, location: str) -> str:
        if title == "Fin del Mundo":
            return "Uno de los recorridos mas conocidos de la zona, ideal para senderismo, cascadas y fotografia de naturaleza."
        if title == "Cascada Hornoyaco":
            return "Parada natural destacada para viajeros que buscan agua, vegetacion y una experiencia visual potente cerca de Mocoa."
        if title == "Ojo de Dios":
            return "Punto natural muy nombrado dentro de las rutas cercanas a Mocoa, asociado a caminatas y paisajes de agua."
        kind = f"{category} / {item_type}".replace("_", " ")
        return f"{title} aparece como punto de interes tipo {kind} en {display_name}. Puede servir como parada recomendada dentro del recorrido por {location}."

    def _tag_for_type(self, item_type: str, category: str) -> str:
        lowered = f"{category} {item_type}".lower()
        if "waterfall" in lowered:
            return "Cascada"
        if "peak" in lowered or "viewpoint" in lowered:
            return "Mirador"
        if "park" in lowered:
            return "Naturaleza"
        return "Sitio turistico"

    def _curated_fallback(self, location: str) -> list[dict]:
        if "mocoa" in location.lower():
            return [
                {
                    "tag": "Cascada",
                    "title": "Fin del Mundo",
                    "description": "Sendero y entorno natural muy reconocido en Mocoa para quienes buscan cascadas, bosque y fotografia.",
                    "schedule": "Horario referencial: 8:00 a. m. - 4:30 p. m.",
                    "price": "Ingreso referencial: COP 10.000 - 30.000 segun acceso y guia.",
                    "duration": "Tiempo recomendado: 2 a 4 horas.",
                    "tips": "Mejor salir temprano y revisar clima antes del recorrido.",
                    "what_to_bring": "Zapatos con agarre, agua, impermeable y protector solar.",
                    "note": "Confirma ingreso, guianza y condiciones del sendero el mismo dia.",
                },
                {
                    "tag": "Cascada",
                    "title": "Cascada Hornoyaco",
                    "description": "Sitio natural cercano a Mocoa que suele aparecer entre las rutas visualmente mas llamativas del destino.",
                    "schedule": "Horario referencial: 8:00 a. m. - 4:00 p. m.",
                    "price": "Costo referencial: validar transporte, guia o acceso segun operador.",
                    "duration": "Tiempo recomendado: 2 a 3 horas.",
                    "tips": "Consulta el acceso vial y si la temporada de lluvias permite una visita comoda.",
                    "what_to_bring": "Ropa comoda, agua, bolsa impermeable y celular con bateria.",
                    "note": "Puede requerir apoyo local para ubicar el acceso mas conveniente.",
                },
                {
                    "tag": "Naturaleza",
                    "title": "Ojo de Dios",
                    "description": "Parada natural muy mencionada dentro del circuito turistico de Mocoa para caminatas y paisajes de agua.",
                    "schedule": "Horario referencial: visita diurna recomendada.",
                    "price": "Precio referencial: consulta con operadores o comunidad local.",
                    "duration": "Tiempo recomendado: 1.5 a 3 horas.",
                    "tips": "Prioriza calzado con agarre y evita ir con lluvia intensa.",
                    "what_to_bring": "Agua, gorra, impermeable ligero y snack.",
                    "note": "Verifica nombre exacto del acceso y condiciones del terreno antes de salir.",
                },
            ]
        return []
