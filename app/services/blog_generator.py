from __future__ import annotations

import hashlib
import json
from html import escape
from pathlib import Path

import bleach
from slugify import slugify
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AgentLog, Blog, BlogMessage, BlogVersion, PromptHistory, User
from app.services.media_library_service import MediaLibraryService
from app.services.ollama_service import OllamaService
from app.services.openclaw_service import OpenClawService
from app.services.topic_research_service import TopicResearchService
from app.services.vector_service import VectorService


class BlogGeneratorService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.ollama = OllamaService()
        self.openclaw = OpenClawService()
        self.vector = VectorService()
        self.media_library = MediaLibraryService()
        self.topic_research = TopicResearchService()

    async def create_blog(self, db: Session, owner_username: str, prompt: str, telegram_chat_id: str | None = None) -> Blog:
        prompt = self._sanitize_text(prompt)
        owner = self._get_or_create_owner(db, owner_username, telegram_chat_id=telegram_chat_id)
        coordinator_result = await self.openclaw.dispatch(
            "create_blog",
            {
                "username": owner_username,
                "prompt": prompt,
                "niche_guess": self._guess_niche(prompt),
                "topic_guess": self._extract_topic(prompt, self._guess_niche(prompt)),
                "guide_mode": self._guide_mode_for_prompt(prompt, self._guess_niche(prompt)),
            },
        )
        prompt_record = PromptHistory(blog_id=None, prompt_type="create_blog", prompt_text=prompt, response_text=None)
        db.add(prompt_record)
        db.flush()

        generated = await self.ollama.generate(
            self._briefing_prompt(),
            prompt,
        )
        data = self._normalize_generation(prompt, generated)
        topic = self._extract_topic(prompt, data["niche"])
        research = await self.topic_research.research(topic, data["niche"], prompt)
        visual_index = self._next_visual_index(db, data["niche"])
        recent_visuals = self._recent_visual_history(db, data["niche"])
        data["palette"] = self._palette_for_request(data["niche"], prompt, data.get("palette"), visual_index)
        data["style_variant"] = self._style_variant_for_request(data["niche"], prompt, visual_index, recent_visuals)
        places: list[dict] = []
        data["content"] = self._build_content_blueprint(prompt, data, places, research)
        data["images"] = await self.media_library.search_images(
            data["content"]["image_query"],
            limit=6,
            niche=data["niche"],
            topic=data["content"].get("topic", data["title"]),
        )
        slug = self._ensure_unique_slug(db, slugify(data["title"]) or "blog")
        blog = Blog(
            owner_id=owner.id,
            title=data["title"],
            slug=slug,
            niche=data["niche"],
            target_audience=data["target_audience"],
            palette=data["palette"],
            design_style=data["design_style"],
            brief=prompt,
            status="draft",
            preview_url=f"{self.settings.public_base_url}/preview/{slug}",
            published_url=None,
            metadata_json={
                "sections": data["sections"],
                "seo_description": data["seo_description"],
                "coordinator_result": coordinator_result,
                "content": data["content"],
                "images": data["images"],
                "location": data["content"]["location"],
                "places": places,
                "research": research,
                "references": data["content"].get("references", []),
            },
            embedding=self.vector.embed_text(prompt),
        )
        db.add(blog)
        db.flush()

        version = self._build_version(blog, "Version inicial generada por IA", 1)
        db.add(version)
        db.flush()
        blog.current_version_id = version.id
        prompt_record.blog_id = blog.id
        prompt_record.response_text = json.dumps(data)
        prompt_record.embedding = self.vector.embed_text(prompt)
        db.add(
            BlogMessage(
                blog_id=blog.id,
                user_id=owner.id,
                channel="telegram",
                role="user",
                message=prompt,
                embedding=self.vector.embed_text(prompt),
            )
        )
        db.add(
            AgentLog(
                task_type="create_blog",
                status="completed",
                request_payload={"prompt": prompt, "owner_username": owner_username},
                response_payload={"blog_id": blog.id, "slug": blog.slug},
            )
        )
        self._write_generated_files(blog.slug, version)
        db.commit()
        db.refresh(blog)
        return blog

    async def edit_blog(self, db: Session, blog: Blog, instruction: str) -> BlogVersion:
        instruction = self._sanitize_text(instruction)
        current = blog.current_version
        next_version_number = (current.version_number if current else 0) + 1
        base_content = blog.metadata_json.get("content") or self._build_content_blueprint(
            blog.brief,
            {
                "title": blog.title,
                "niche": blog.niche,
                "target_audience": blog.target_audience,
                "tone": "cercano, inspirador y confiable",
                "promise": blog.metadata_json.get("seo_description", blog.brief),
                "primary_cta": self._default_cta_for_niche(blog.niche),
                "faqs": [],
            },
            blog.metadata_json.get("places", []),
            blog.metadata_json.get("research", self._fallback_research(blog.brief, blog.niche)),
        )
        updated_content = self._apply_instruction(base_content, instruction, blog)
        updated_images = blog.metadata_json.get("images", [])
        places = blog.metadata_json.get("places", [])
        if any(word in instruction.lower() for word in ("imagen", "foto", "galeria", "sitio turistico", "paisaje")):
            query = updated_content.get("image_query") or f"{blog.niche} {updated_content.get('location', '')}"
            updated_images = await self.media_library.search_images(
                query,
                limit=6,
                niche=blog.niche,
                topic=updated_content.get("topic", blog.title),
            )

        blog.metadata_json = {
            **blog.metadata_json,
            "content": updated_content,
            "images": updated_images,
            "places": places,
            "references": updated_content.get("references", blog.metadata_json.get("references", [])),
            "last_instruction": instruction,
        }
        version = self._build_version(blog, instruction, next_version_number)
        db.add(version)
        db.flush()
        blog.current_version_id = version.id
        blog.status = "draft"
        db.add(
            PromptHistory(
                blog_id=blog.id,
                prompt_type="edit_blog",
                prompt_text=instruction,
                response_text=version.change_summary,
                embedding=self.vector.embed_text(instruction),
            )
        )
        self._write_generated_files(blog.slug, version)
        db.commit()
        db.refresh(version)
        return version

    def publish_blog(self, db: Session, blog: Blog) -> Blog:
        blog.status = "published"
        blog.published_url = f"{self.settings.public_base_url}/preview/{blog.slug}?published=1"
        db.commit()
        db.refresh(blog)
        return blog

    def _get_or_create_owner(self, db: Session, owner_username: str, telegram_chat_id: str | None = None) -> User:
        owner = db.scalar(select(User).where(User.username == owner_username))
        if owner:
            if telegram_chat_id and owner.telegram_chat_id != telegram_chat_id:
                owner.telegram_chat_id = telegram_chat_id
            return owner
        owner = User(
            username=owner_username,
            full_name=owner_username.title(),
            telegram_chat_id=telegram_chat_id,
            preferences={"source": "auto"},
        )
        db.add(owner)
        db.flush()
        return owner

    def _ensure_unique_slug(self, db: Session, base_slug: str) -> str:
        candidate = base_slug
        index = 1
        while db.scalar(select(Blog).where(Blog.slug == candidate)):
            index += 1
            candidate = f"{base_slug}-{index}"
        return candidate

    def _briefing_prompt(self) -> str:
        return (
            "Eres un estratega editorial y diseñador web. Responde solo JSON valido con estas claves: "
            "title, niche, target_audience, design_style, palette, sections, seo_description, tone, promise, "
            "primary_cta, highlights, faqs. No incluyas markdown."
        )

    def _normalize_generation(self, prompt: str, raw: str) -> dict:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        raw_title = self._sanitize_text(data.get("title") or self._title_from_prompt(prompt))
        title = self._clean_generated_title(raw_title, prompt)
        niche = self._sanitize_text(data.get("niche") or self._guess_niche(prompt))
        location = self._extract_location(prompt)
        return {
            "title": title,
            "niche": niche,
            "target_audience": self._sanitize_text(
                data.get("target_audience") or self._default_audience_for_niche(niche, location)
            ),
            "design_style": self._sanitize_text(data.get("design_style") or "editorial-moderno"),
            "palette": self._palette_for_request(niche, prompt, data.get("palette")),
            "sections": data.get("sections") or self._default_sections_for_niche(niche),
            "seo_description": self._sanitize_text(
                data.get("seo_description") or self._default_seo_description(niche, prompt)
            ),
            "tone": self._sanitize_text(data.get("tone") or "cercano, inspirador y confiable"),
            "promise": self._sanitize_text(
                data.get("promise") or self._default_promise_for_niche(niche, prompt)
            ),
            "primary_cta": self._sanitize_text(data.get("primary_cta") or self._default_cta_for_niche(niche)),
            "highlights": data.get("highlights") or [],
            "faqs": data.get("faqs") or [],
        }

    def _build_content_blueprint(self, prompt: str, data: dict, places: list[dict]) -> dict:
        if data["niche"] != "turismo":
            return self._build_generic_blueprint(prompt, data)

        location = self._extract_location(prompt)
        niche = data["niche"]
        audience = data["target_audience"]
        title = data["title"]
        tone = data["tone"]
        promise = data["promise"]
        destinations = places or self._default_destinations(niche, location)
        return {
            "location": location,
            "topic": location,
            "style_variant": data.get("style_variant", self._style_variant_for_request(niche, prompt, 0)),
            "image_query": self._build_image_query(niche, location),
            "hero_kicker": location.upper(),
            "hero_headline": title,
            "hero_intro": promise,
            "hero_support": (
                f"Este blog esta pensado para {audience.lower()} con recomendaciones practicas, "
                f"lugares fotogenicos, consejos logistico-turisticos y un tono {tone}."
            ),
            "cta_label": data["primary_cta"],
            "about_title": f"Por que visitar {location}",
            "about_paragraphs": [
                f"{location} combina naturaleza, identidad regional y experiencias memorables para viajeros que buscan algo mas que un destino rapido.",
                f"Desde planes de un dia hasta rutas mas completas, este blog organiza lo esencial para que la visita se sienta clara, atractiva y lista para compartir.",
            ],
            "practical_note": (
                "Los horarios, precios y tiempos de visita mostrados aqui son referenciales para orientar al usuario final. "
                "Antes de viajar conviene confirmarlos con operadores, redes oficiales o comercios locales."
            ),
            "featured_destinations": destinations,
            "experience_blocks": [
                {
                    "title": "Naturaleza viva",
                    "description": f"Rutas verdes, miradores y espacios ideales para conectar con la esencia natural de {location}.",
                },
                {
                    "title": "Cultura local",
                    "description": "Paradas con comida regional, historias del territorio y recomendaciones para una visita mas consciente.",
                },
                {
                    "title": "Viaje practico",
                    "description": "Consejos de clima, movilidad, tiempos y que llevar para que la experiencia sea mas comoda.",
                },
            ],
            "itinerary_title": "Ruta recomendada",
            "itinerary_steps": [
                "Empieza temprano con un recorrido panoramico y puntos para fotografia de paisaje.",
                "Reserva la franja central del dia para gastronomia local y descanso en un entorno natural.",
                "Cierra con una actividad tranquila, compra de recuerdos y contenido visual para redes o promocion.",
            ],
            "faq_items": self._build_faqs(data, location),
            "contact_title": f"Organiza tu experiencia en {location}",
            "contact_copy": "Usa este espacio para cotizaciones, reservas guiadas, convenios con hoteles o paquetes turisticos.",
            "credits_note": (
                "Las imagenes externas provienen de buscadores y bancos compatibles con atribucion visible. "
                "Cada imagen muestra autor, fuente y licencia; revisa siempre esas condiciones antes de reutilizarla comercialmente."
            ),
            "nav_primary": "Sobre",
            "nav_secondary": "Destinos",
            "nav_tertiary": "Galeria",
            "nav_quaternary": "FAQ",
            "section_kicker": "Lugares destacados",
            "section_title": "Experiencias que vale la pena mostrar",
            "section_note": "Cada bloque ya viene redactado como contenido final, no como una instruccion pendiente.",
            "experience_kicker": "Que encuentra el visitante",
            "experience_title": "Contenido listo para inspirar y convertir",
            "gallery_title": "Imagenes de referencia con autoria visible",
        }

    def _build_generic_blueprint(self, prompt: str, data: dict) -> dict:
        niche = data["niche"]
        topic = self._extract_topic(prompt, niche)
        audience = data["target_audience"]
        title = data["title"]
        tone = data["tone"]
        image_query = self._build_image_query(niche, topic)

        if niche == "noticias":
            return {
                "location": topic,
                "style_variant": data.get("style_variant", self._style_variant_for_request(niche, prompt, 0)),
                "template_family": self._template_family_for_variant(data.get("style_variant", "")),
                "image_query": image_query,
                "topic": topic,
                "hero_kicker": "ACTUALIDAD",
                "hero_headline": title,
                "hero_intro": f"Cobertura clara, ordenada y atractiva sobre {topic}.",
                "hero_support": f"Este blog esta pensado para {audience.lower()} con enfoque {tone}, titulares claros, contexto y lectura rapida.",
                "cta_label": "Ver cobertura",
                "about_title": f"Por que seguir noticias sobre {topic}",
                "about_paragraphs": [
                    f"Este blog organiza contenido sobre {topic} con una estructura facil de consumir para lectores que quieren contexto sin perder agilidad.",
                    "La idea es mezclar titulares, analisis breve, seguimiento de temas y bloques visuales para que la experiencia no se sienta plana.",
                ],
                "practical_note": "Los datos de ejemplo deben actualizarse con hechos reales y verificados antes de publicarse como cobertura final.",
                "featured_destinations": self._default_feature_cards(niche, topic),
                "experience_blocks": self._default_experience_blocks(niche, topic),
                "itinerary_title": "Linea editorial sugerida",
                "itinerary_steps": [
                    "Abre con el titular principal y una bajada que explique por que importa.",
                    "Sigue con contexto, voces clave y datos que ayuden a entender el tema.",
                    "Cierra con seguimiento, enlaces relacionados y una llamada a suscribirse.",
                ],
                "faq_items": self._build_faqs(data, topic),
                "contact_title": "Recibe alertas y novedades",
                "contact_copy": "Usa este bloque para suscripciones, envios de boletin o contacto editorial.",
                "credits_note": "Las imagenes se usan como apoyo visual. Revisa atribucion y licencia antes de publicarlas.",
                "nav_primary": "Editorial",
                "nav_secondary": "Cobertura",
                "nav_tertiary": "Galeria",
                "nav_quaternary": "FAQ",
                "section_kicker": "Cobertura principal",
                "section_title": "Temas y frentes informativos",
                "section_note": "Cada bloque se enfoca en una linea noticiosa distinta para evitar que todo parezca el mismo artículo.",
                "experience_kicker": "Propuesta de valor",
                "experience_title": "Como hacer que el blog se sienta vivo y actual",
                "gallery_title": "Visuales para acompañar la cobertura",
            }

        if niche == "medio_ambiente":
            return {
                "location": topic,
                "style_variant": data.get("style_variant", self._style_variant_for_request(niche, prompt, 0)),
                "template_family": self._template_family_for_variant(data.get("style_variant", "")),
                "image_query": image_query,
                "topic": topic,
                "hero_kicker": "SOSTENIBILIDAD",
                "hero_headline": title,
                "hero_intro": f"Contenido para explicar, inspirar y movilizar conversaciones sobre {topic}.",
                "hero_support": f"Este blog esta pensado para {audience.lower()} con enfoque {tone}, pedagogia clara y accion practica.",
                "cta_label": "Explorar contenido",
                "about_title": f"Por que hablar de {topic}",
                "about_paragraphs": [
                    f"{topic} puede abordarse desde educacion, comunidad, cambio de habitos, biodiversidad, reciclaje o impacto territorial.",
                    "La estructura del blog busca que el lector entienda el problema, vea ejemplos y encuentre acciones concretas para involucrarse.",
                ],
                "practical_note": "Si el blog se va a publicar como referencia tecnica, agrega fuentes verificables y cifras actualizadas.",
                "featured_destinations": self._default_feature_cards(niche, topic),
                "experience_blocks": self._default_experience_blocks(niche, topic),
                "itinerary_title": "Ruta de lectura recomendada",
                "itinerary_steps": [
                    "Empieza con el problema o reto ambiental que quieres poner sobre la mesa.",
                    "Continua con ejemplos, causas, impactos y acciones posibles.",
                    "Cierra con recursos, llamados a participar o iniciativas destacadas.",
                ],
                "faq_items": self._build_faqs(data, topic),
                "contact_title": "Conecta con la comunidad",
                "contact_copy": "Usa este espacio para recibir voluntarios, alianzas, preguntas o propuestas de proyectos sostenibles.",
                "credits_note": "Las imagenes apoyan la narrativa del tema y deben revisarse antes de reutilizacion comercial.",
                "nav_primary": "Contexto",
                "nav_secondary": "Temas",
                "nav_tertiary": "Galeria",
                "nav_quaternary": "FAQ",
                "section_kicker": "Temas clave",
                "section_title": "Frentes editoriales sobre medio ambiente",
                "section_note": "El blog debe aterrizar el tema en problemas, soluciones y acciones concretas.",
                "experience_kicker": "Enfoque del blog",
                "experience_title": "Contenido que informa y moviliza",
                "gallery_title": "Imagenes para reforzar el mensaje",
            }

        if niche == "deportes":
            if self._is_sports_event_prompt(prompt, topic):
                return {
                    "location": topic,
                    "style_variant": data.get("style_variant", self._style_variant_for_request(niche, prompt, 0)),
                    "template_family": self._template_family_for_variant(data.get("style_variant", "")),
                    "image_query": self._build_sports_event_image_query(topic),
                    "topic": topic,
                    "hero_kicker": "EVENTO DEPORTIVO",
                    "hero_headline": title,
                    "hero_intro": f"Cobertura visual sobre {topic} con foco en selecciones, fechas, calendario y contexto del torneo.",
                    "hero_support": f"Este blog esta dirigido a {audience.lower()} con tono {tone}, lectura rapida y secciones pensadas para seguir el evento sin ruido.",
                    "cta_label": "Ver calendario",
                    "about_title": f"Como cubrir {topic} sin caer en una plantilla vacia",
                    "about_paragraphs": [
                        f"El enfoque editorial de {topic} debe mezclar calendario, paises participantes, cruces, sedes y expectativa del torneo.",
                        "La pagina debe sentirse como una portada especial del evento: clara, intensa y util para quien quiere entender rapido que se juega, cuando y por que importa.",
                    ],
                    "practical_note": "Si el blog va a publicar datos oficiales de clasificacion o fixture, valida los cambios mas recientes antes de publicar.",
                    "featured_destinations": self._sports_event_feature_cards(topic),
                    "experience_blocks": self._sports_event_experience_blocks(topic),
                    "itinerary_title": "Recorrido editorial del torneo",
                    "itinerary_steps": [
                        "Abre con panorama general del torneo y por que esta edicion importa.",
                        "Continua con selecciones, grupos, fechas clave y partidos mas esperados.",
                        "Cierra con figuras, sedes, expectativas y llamada a seguir la cobertura.",
                    ],
                    "faq_items": self._sports_event_faqs(topic),
                    "contact_title": "Recibe cobertura del torneo",
                    "contact_copy": "Usa este bloque para suscripciones, alertas del torneo, comunidad o patrocinio editorial.",
                    "credits_note": "Las imagenes deportivas deben revisarse cuidadosamente antes de reutilizacion publica o comercial.",
                    "nav_primary": "Panorama",
                    "nav_secondary": "Torneo",
                    "nav_tertiary": "Galeria",
                    "nav_quaternary": "FAQ",
                    "section_kicker": "Cobertura del torneo",
                    "section_title": "Selecciones, fechas y bloques clave del evento",
                    "section_note": "La pagina debe centrarse en el torneo pedido, no en una estructura corporativa generica.",
                    "experience_kicker": "Experiencia del lector",
                    "experience_title": "Ritmo, fixture y expectativa",
                    "gallery_title": "Visuales del torneo y ambiente competitivo",
                }
            return {
                "location": topic,
                "style_variant": data.get("style_variant", self._style_variant_for_request(niche, prompt, 0)),
                "template_family": self._template_family_for_variant(data.get("style_variant", "")),
                "image_query": image_query,
                "topic": topic,
                "hero_kicker": "DEPORTE",
                "hero_headline": title,
                "hero_intro": f"Un blog pensado para contar, analizar y destacar todo lo relacionado con {topic}.",
                "hero_support": f"Este blog esta dirigido a {audience.lower()} con un tono {tone} y una mezcla de actualidad, analisis y comunidad.",
                "cta_label": "Ver secciones",
                "about_title": f"Como cubrir {topic} con personalidad",
                "about_paragraphs": [
                    "La mejor experiencia deportiva mezcla emocion, contexto, calendario, protagonistas y comunidad.",
                    f"En vez de parecer una pagina genérica, este blog busca que {topic} tenga narrativa, ritmo y visuales con energia.",
                ],
                "practical_note": "Si publicas resultados o calendario, valida siempre datos recientes antes de exponerlos como oficiales.",
                "featured_destinations": self._default_feature_cards(niche, topic),
                "experience_blocks": self._default_experience_blocks(niche, topic),
                "itinerary_title": "Estructura editorial sugerida",
                "itinerary_steps": [
                    "Abre con una portada fuerte y protagonistas claros.",
                    "Sigue con analisis, agenda, historias y momentos destacados.",
                    "Cierra con comunidad, comentarios o suscripcion.",
                ],
                "faq_items": self._build_faqs(data, topic),
                "contact_title": "Conecta con la audiencia deportiva",
                "contact_copy": "Usa este bloque para newsletter, membresias, contacto comercial o comunidad del blog.",
                "credits_note": "Verifica siempre atribucion y licencia antes de usar imagenes deportivas en publico.",
                "nav_primary": "Editorial",
                "nav_secondary": "Secciones",
                "nav_tertiary": "Galeria",
                "nav_quaternary": "FAQ",
                "section_kicker": "Secciones destacadas",
                "section_title": "Como organizar un blog deportivo completo",
                "section_note": "La idea es que el usuario sienta dinamismo, actualidad y personalidad visual.",
                "experience_kicker": "Experiencia lectora",
                "experience_title": "Ritmo, analisis y comunidad",
                "gallery_title": "Imagenes para reforzar energia y contexto",
            }

        return {
            "location": topic,
            "style_variant": data.get("style_variant", self._style_variant_for_request(niche, prompt, 0)),
            "template_family": self._template_family_for_variant(data.get("style_variant", "")),
            "image_query": image_query,
            "topic": topic,
            "hero_kicker": niche.upper().replace("_", " "),
            "hero_headline": title,
            "hero_intro": data["promise"],
            "hero_support": f"Este blog esta pensado para {audience.lower()} con enfoque {tone}, estructura clara y una experiencia lista para afinarse en tiempo real.",
            "cta_label": data["primary_cta"],
            "about_title": f"Como presentar {topic}",
            "about_paragraphs": [
                f"El blog toma como eje principal {topic} y organiza el contenido para que se sienta coherente con el nicho.",
                "La base ya sale preparada para verse como un sitio completo y luego puede ajustarse con nuevas instrucciones.",
            ],
            "practical_note": "Puedes usar esta base como primer borrador serio y seguir ajustandola en tiempo real desde el panel.",
            "featured_destinations": self._default_feature_cards(niche, topic),
            "experience_blocks": self._default_experience_blocks(niche, topic),
            "itinerary_title": "Secuencia sugerida",
            "itinerary_steps": [
                "Abre con un mensaje principal claro.",
                "Desarrolla bloques que profundicen el tema desde distintos angulos.",
                "Cierra con una llamada a la accion o formulario.",
            ],
            "faq_items": self._build_faqs(data, topic),
            "contact_title": "Hablemos del proyecto",
            "contact_copy": "Usa este bloque para contacto, conversion, propuestas o captacion de leads.",
            "credits_note": "Las imagenes externas deben revisarse antes de publicacion comercial.",
            "nav_primary": "Contexto",
            "nav_secondary": "Secciones",
            "nav_tertiary": "Galeria",
            "nav_quaternary": "FAQ",
            "section_kicker": "Secciones clave",
            "section_title": "Bloques principales del blog",
            "section_note": "Cada seccion debe sostener el tema pedido y no reciclar el enfoque de otro nicho.",
            "experience_kicker": "Experiencia del sitio",
            "experience_title": "Una base lista para personalizar",
            "gallery_title": "Visuales de apoyo",
        }

    def _build_content_blueprint(self, prompt: str, data: dict, places: list[dict], research: dict | None = None) -> dict:
        if data["niche"] != "turismo":
            return self._build_generic_blueprint(prompt, data, research or self._fallback_research(prompt, data["niche"]))

        location = self._extract_location(prompt)
        niche = data["niche"]
        audience = data["target_audience"]
        title = data["title"]
        tone = data["tone"]
        promise = data["promise"]
        destinations = places or self._default_destinations(niche, location)
        return {
            "location": location,
            "topic": location,
            "style_variant": data.get("style_variant", self._style_variant_for_request(niche, prompt, 0)),
            "image_query": self._build_image_query(niche, location),
            "hero_kicker": location.upper(),
            "hero_headline": title,
            "hero_intro": promise,
            "hero_support": (
                f"Este blog esta pensado para {audience.lower()} con recomendaciones practicas, "
                f"lugares fotogenicos, consejos logistico-turisticos y un tono {tone}."
            ),
            "cta_label": data["primary_cta"],
            "about_title": f"Por que visitar {location}",
            "about_paragraphs": [
                f"{location} combina naturaleza, identidad regional y experiencias memorables para viajeros que buscan algo mas que un destino rapido.",
                f"Desde planes de un dia hasta rutas mas completas, este blog organiza lo esencial para que la visita se sienta clara, atractiva y lista para compartir.",
            ],
            "practical_note": (
                "Los horarios, precios y tiempos de visita mostrados aqui son referenciales para orientar al usuario final. "
                "Antes de viajar conviene confirmarlos con operadores, redes oficiales o comercios locales."
            ),
            "featured_destinations": destinations,
            "experience_blocks": [
                {"title": "Naturaleza viva", "description": f"Rutas verdes, miradores y espacios ideales para conectar con la esencia natural de {location}."},
                {"title": "Cultura local", "description": "Paradas con comida regional, historias del territorio y recomendaciones para una visita mas consciente."},
                {"title": "Viaje practico", "description": "Consejos de clima, movilidad, tiempos y que llevar para que la experiencia sea mas comoda."},
            ],
            "itinerary_title": "Ruta recomendada",
            "itinerary_steps": [
                "Empieza temprano con un recorrido panoramico y puntos para fotografia de paisaje.",
                "Reserva la franja central del dia para gastronomia local y descanso en un entorno natural.",
                "Cierra con una actividad tranquila, compra de recuerdos y contenido visual para redes o promocion.",
            ],
            "faq_items": self._build_faqs(data, location),
            "contact_title": f"Organiza tu experiencia en {location}",
            "contact_copy": "Usa este espacio para cotizaciones, reservas guiadas, convenios con hoteles o paquetes turisticos.",
            "credits_note": (
                "Las imagenes externas provienen de buscadores y bancos compatibles con atribucion visible. "
                "Cada imagen muestra autor, fuente y licencia; revisa siempre esas condiciones antes de reutilizarla comercialmente."
            ),
            "nav_primary": "Sobre",
            "nav_secondary": "Destinos",
            "nav_tertiary": "Galeria",
            "nav_quaternary": "FAQ",
            "section_kicker": "Lugares destacados",
            "section_title": "Experiencias que vale la pena mostrar",
            "section_note": "Cada bloque ya viene redactado como contenido final, no como una instruccion pendiente.",
            "experience_kicker": "Que encuentra el visitante",
            "experience_title": "Contenido listo para inspirar y convertir",
            "gallery_title": "Imagenes de referencia con autoria visible",
        }

    def _build_generic_blueprint(self, prompt: str, data: dict, research: dict) -> dict:
        niche = data["niche"]
        topic = self._extract_topic(prompt, niche)
        audience = data["target_audience"]
        title = data["title"]
        tone = data["tone"]
        summary = self._sanitize_text(research.get("summary") or self._default_promise_for_niche(niche, prompt))
        focus_phrase = research.get("focus_phrase", topic)
        image_query = research.get("image_query") or self._build_image_query(niche, focus_phrase)
        is_sports_event = niche == "deportes" and self._is_sports_event_prompt(prompt, topic)
        guide_mode = self._guide_mode_for_prompt(prompt, niche)

        hero_kicker_map = {
            "noticias": "ACTUALIDAD",
            "medio_ambiente": "SOSTENIBILIDAD",
            "deportes": "EVENTO DEPORTIVO" if is_sports_event else "DEPORTE",
            "tecnologia": "TECNOLOGIA",
            "cafeteria": "EXPERIENCIA",
            "corporativo": "MARCA",
        }
        about_title_map = {
            "noticias": f"Por que seguir noticias sobre {topic}",
            "medio_ambiente": f"Por que hablar de {topic}",
            "deportes": f"Como cubrir {topic} con personalidad",
            "tecnologia": f"Como explicar {topic} con claridad",
            "cafeteria": f"Como presentar {topic} con identidad",
            "corporativo": f"Como convertir {topic} en una propuesta clara",
        }
        section_kicker_map = {
            "noticias": "Cobertura principal",
            "medio_ambiente": "Temas clave",
            "deportes": "Cobertura del torneo" if is_sports_event else "Secciones destacadas",
            "tecnologia": "Claves del tema",
            "cafeteria": "Momentos destacados",
            "corporativo": "Bloques estrategicos",
        }
        section_title_map = {
            "noticias": "Frentes editoriales construidos con informacion real",
            "medio_ambiente": "Hallazgos, impacto y acciones posibles",
            "deportes": "Selecciones, agenda y focos del evento" if is_sports_event else "Cobertura, protagonistas y lectura del juego",
            "tecnologia": "Ideas, utilidades y preguntas del tema",
            "cafeteria": "Producto, atmosfera y conversion",
            "corporativo": "Contexto, oferta y cierre comercial",
        }
        nav_primary_map = {
            "noticias": "Editorial",
            "medio_ambiente": "Contexto",
            "deportes": "Panorama" if is_sports_event else "Editorial",
            "tecnologia": "Producto",
            "cafeteria": "Experiencia",
            "corporativo": "Propuesta",
        }
        nav_secondary_map = {
            "noticias": "Cobertura",
            "medio_ambiente": "Temas",
            "deportes": "Torneo" if is_sports_event else "Secciones",
            "tecnologia": "Analisis",
            "cafeteria": "Carta",
            "corporativo": "Servicios",
        }
        contact_title_map = {
            "noticias": "Recibe alertas y novedades",
            "medio_ambiente": "Conecta con la comunidad",
            "deportes": "Conecta con la audiencia deportiva",
            "tecnologia": "Solicita demo o consultoria",
            "cafeteria": "Reserva, pide o pregunta",
            "corporativo": "Hablemos del proyecto",
        }
        experience_title_map = {
            "noticias": "Como hacer que el blog se sienta vivo y actual",
            "medio_ambiente": "Contenido que informa y moviliza",
            "deportes": "Ritmo, analisis y seguimiento",
            "tecnologia": "Un blog que traduce ideas en utilidad",
            "cafeteria": "Una experiencia lista para enamorar y vender",
            "corporativo": "Una base completa lista para convertir",
        }
        gallery_title_map = {
            "noticias": "Visuales para acompanar la cobertura",
            "medio_ambiente": "Imagenes para reforzar el mensaje",
            "deportes": "Visuales del tema y ambiente competitivo" if is_sports_event else "Imagenes para reforzar energia y contexto",
            "tecnologia": "Imagenes alineadas al tema",
            "cafeteria": "Visuales para abrir apetito e identidad",
            "corporativo": "Imagenes de apoyo",
        }

        return {
            "location": topic,
            "style_variant": data.get("style_variant", self._style_variant_for_request(niche, prompt, 0)),
            "template_family": self._template_family_for_variant(data.get("style_variant", "")),
            "image_query": image_query,
            "topic": topic,
            "focus_phrase": focus_phrase,
            "hero_kicker": hero_kicker_map.get(niche, niche.upper().replace("_", " ")),
            "hero_headline": title,
            "hero_intro": summary,
            "hero_support": f"Este blog esta pensado para {audience.lower()} con enfoque {tone}, contenido desarrollado desde {focus_phrase.lower()}, imagenes relacionadas y referencias para profundizar.",
            "cta_label": data["primary_cta"],
            "about_title": about_title_map.get(niche, f"Como presentar {topic}"),
            "about_paragraphs": self._about_paragraphs_from_research(topic, niche, summary, research),
            "practical_note": research.get("citation_note") or "Valida cualquier dato sensible antes de publicarlo.",
            "featured_destinations": self._sports_event_feature_cards(topic, research, prompt) if is_sports_event else self._feature_cards_from_research(niche, topic, research),
            "experience_blocks": self._sports_event_experience_blocks(topic, research, prompt) if is_sports_event else self._experience_blocks_from_research(niche, topic, research),
            "itinerary_title": "Ruta editorial sugerida",
            "itinerary_steps": self._sports_event_itinerary(topic, research, prompt) if is_sports_event else self._itinerary_steps_from_research(niche, topic, research),
            "faq_items": self._sports_event_faqs(topic, research, prompt) if is_sports_event else self._faq_items_from_research(data, topic, research),
            "article_title": self._article_title_for_niche(niche, topic),
            "article_intro": self._article_intro_for_niche(niche, topic, summary),
            "article_sections": self._sports_guide_sections(topic, research, prompt) if is_sports_event or guide_mode == "guide" else self._article_sections_from_research(niche, topic, research),
            "references": research.get("references", []),
            "references_title": self._references_title_for_niche(niche),
            "references_intro": self._references_intro_for_niche(niche, research),
            "contact_title": contact_title_map.get(niche, "Hablemos del proyecto"),
            "contact_copy": self._contact_copy_for_niche(niche),
            "credits_note": "Las imagenes deben revisarse con su atribucion y licencia antes de reutilizacion publica o comercial.",
            "nav_primary": nav_primary_map.get(niche, "Contexto"),
            "nav_secondary": nav_secondary_map.get(niche, "Secciones"),
            "nav_tertiary": "Galeria",
            "nav_quaternary": "Referencias",
            "section_kicker": section_kicker_map.get(niche, "Secciones clave"),
            "section_title": section_title_map.get(niche, "Bloques principales del tema"),
            "section_note": "Cada bloque usa el tema pedido como eje y evita quedarse en un cascaron de plantilla.",
            "experience_kicker": "Experiencia del blog",
            "experience_title": experience_title_map.get(niche, "Una base completa lista para afinar"),
            "gallery_title": gallery_title_map.get(niche, "Visuales de apoyo"),
        }

    def _default_feature_cards(self, niche: str, topic: str) -> list[dict]:
        if niche == "noticias":
            return [
                self._card("Portada", "Titular principal", f"El tema {topic} necesita una portada fuerte con contexto, relevancia y una lectura inmediata."),
                self._card("Seguimiento", "Cobertura continua", "Espacio para notas relacionadas, actualizaciones y piezas que mantengan vivo el sitio."),
                self._card("Analisis", "Claves para entender el tema", "Bloques que ayuden a profundizar mas alla del titular."),
            ]
        if niche == "medio_ambiente":
            return [
                self._card("Problema", "Reto ambiental", f"Explica por que {topic} importa y cual es el impacto real en comunidad, territorio o habitos."),
                self._card("Soluciones", "Acciones posibles", "Presenta iniciativas, cambios de comportamiento y respuestas aplicables."),
                self._card("Comunidad", "Historias y ejemplos", "Muestra personas, proyectos o aprendizajes que vuelven cercano el tema."),
            ]
        if niche == "deportes":
            return [
                self._card("Agenda", "Calendario y seguimiento", f"Organiza fechas, focos y protagonistas alrededor de {topic}."),
                self._card("Historias", "Perfiles y momentos", "Da espacio a jugadores, equipos, procesos o hitos importantes."),
                self._card("Analisis", "Lectura del juego", "Incluye opinion, tendencias y contexto para enganchar a la audiencia."),
            ]
        if niche == "tecnologia":
            return [
                self._card("Producto", "Tema principal", f"Explica {topic} desde utilidad, innovacion y experiencia real para el usuario."),
                self._card("Analisis", "Que cambia y por que importa", "Baja el tema a beneficios, contexto y comparativas faciles de leer."),
                self._card("Accion", "Demo, contacto o recurso", "Cierra con una accion concreta como prueba, descarga o conversacion."),
            ]
        if niche == "cafeteria":
            return [
                self._card("Experiencia", "Ambiente y propuesta", f"Presenta {topic} con atmosfera, producto y una identidad facil de recordar."),
                self._card("Carta", "Bebidas y diferenciales", "Muestra preparaciones, origenes, temporada o especialidades destacadas."),
                self._card("Conversion", "Reserva o pedido", "Guia al usuario hacia contacto, pedido, visita o reserva."),
            ]
        if niche == "corporativo":
            return [
                self._card("Propuesta", "Valor principal", f"Resume {topic} con una promesa clara y una presentacion confiable."),
                self._card("Servicios", "Bloques de oferta", "Ordena capacidades, casos o soluciones para una lectura escaneable."),
                self._card("Contacto", "Cierre comercial", "Lleva al usuario a una accion concreta como consulta, agendamiento o formulario."),
            ]
        return [
            self._card("Principal", f"Lo mejor de {topic}", "Un bloque fuerte que deje claro el foco del blog."),
            self._card("Profundiza", "Subtema destacado", "Una seccion para ampliar el angulo principal con utilidad real."),
            self._card("Convierte", "Cierre con accion", "Espacio para guiar al usuario hacia contacto, registro o lectura adicional."),
        ]

    def _default_experience_blocks(self, niche: str, topic: str) -> list[dict]:
        if niche == "noticias":
            return [
                {"title": "Titulares claros", "description": "Una portada que jerarquiza la informacion y evita que todo se vea igual."},
                {"title": "Contexto rapido", "description": f"Bloques cortos para entender {topic} sin perder el hilo informativo."},
                {"title": "Seguimiento editorial", "description": "Relaciona noticias, analisis y piezas derivadas para retener audiencia."},
            ]
        if niche == "medio_ambiente":
            return [
                {"title": "Pedagogia sencilla", "description": f"Traduce {topic} en mensajes accionables y faciles de entender."},
                {"title": "Impacto visible", "description": "Muestra consecuencias, soluciones y ejemplos sin caer en texto plano."},
                {"title": "Participacion", "description": "Invita a la audiencia a aportar, aprender o sumarse a iniciativas."},
            ]
        if niche == "deportes":
            return [
                {"title": "Energia visual", "description": "Haz que la experiencia tenga ritmo, intensidad y dinamismo."},
                {"title": "Agenda y seguimiento", "description": "Facilita volver al sitio para consultar novedades y contexto."},
                {"title": "Comunidad", "description": "Abre espacio a comentarios, suscripciones o identidad de hinchada."},
            ]
        if niche == "tecnologia":
            return [
                {"title": "Utilidad clara", "description": f"Aterriza {topic} en beneficios, flujos o casos de uso comprensibles."},
                {"title": "Look digital", "description": "Haz que el blog se sienta actual, limpio y con jerarquia fuerte."},
                {"title": "Conversion", "description": "Lleva al lector a demo, contacto, recurso o siguiente paso concreto."},
            ]
        if niche == "cafeteria":
            return [
                {"title": "Atmosfera", "description": "Combina producto, espacio y estilo para que el sitio se sienta deseable."},
                {"title": "Producto", "description": "Ordena bebidas, origenes o especiales de forma clara y apetecible."},
                {"title": "Reserva o pedido", "description": "Facilita contacto, visita, pedido o accion comercial inmediata."},
            ]
        if niche == "corporativo":
            return [
                {"title": "Mensaje claro", "description": "Haz que la propuesta principal se entienda en el primer scroll."},
                {"title": "Autoridad", "description": "Sostiene confianza con estructura, orden y enfoque profesional."},
                {"title": "Captacion", "description": "Cierra con contacto, agenda o conversion sin ruido innecesario."},
            ]
        return [
            {"title": "Mensaje claro", "description": f"El tema {topic} debe sentirse protagonista desde el primer scroll."},
            {"title": "Bloques utiles", "description": "Cada seccion debe aportar una razon distinta para seguir leyendo."},
            {"title": "Conversion", "description": "El sitio debe cerrar con una accion clara para el usuario."},
        ]

    def _sports_event_feature_cards(self, topic: str) -> list[dict]:
        return [
            self._card(
                "Selecciones",
                "Paises clasificados y favoritos",
                f"Organiza el bloque principal de {topic} con selecciones confirmadas, candidatas y focos de conversacion."
            ),
            self._card(
                "Calendario",
                "Fechas y partidos clave",
                "Resume inauguracion, jornadas decisivas, cruces esperados y momentos que marcaran la cobertura."
            ),
            self._card(
                "Sedes",
                "Ciudades, estadios y ambiente",
                "Da contexto sobre donde se juega, que escenarios pesan y como cambia la experiencia del torneo."
            ),
        ]

    def _sports_event_experience_blocks(self, topic: str) -> list[dict]:
        return [
            {"title": "Fixture claro", "description": f"Haz que {topic} se entienda rapido con fechas, cruces y lectura escaneable."},
            {"title": "Narrativa de selecciones", "description": "Combina favoritas, sorpresas, grupos y figuras para que la pagina tenga tension."},
            {"title": "Cobertura viva", "description": "Deja espacio a actualizaciones, resultados, analisis y seguimiento durante el torneo."},
        ]

    def _sports_event_faqs(self, topic: str) -> list[dict]:
        return [
            {"question": f"Como estructurar un blog sobre {topic} para que no se vea vacio?", "answer": "Separando panorama, selecciones, fechas, sedes y favoritos en bloques con jerarquia clara."},
            {"question": "Que informacion debe ir primero en un especial deportivo?", "answer": "Lo mas util para el lector: que torneo es, cuando arranca, quienes participan y que partidos generan expectativa."},
            {"question": "Las imagenes del torneo se pueden reutilizar libremente?", "answer": "No siempre. Revisa fuente, autor y licencia antes de publicar o reutilizar visuales del evento."},
        ]

    def _about_paragraphs_from_research(self, topic: str, niche: str, summary: str, research: dict) -> list[str]:
        points = research.get("key_points", [])
        paragraphs = [summary]
        if points:
            first = points[0]
            paragraphs.append(
                f"{first.get('title', topic)}: {first.get('description', '')}".strip()
            )
        if len(points) > 1:
            second = points[1]
            paragraphs.append(
                f"Otro frente relevante para {topic} es {second.get('description', '')}".strip()
            )
        unique = []
        seen = set()
        for paragraph in paragraphs:
            clean = self._sanitize_text(paragraph)
            if not clean or clean in seen:
                continue
            seen.add(clean)
            unique.append(clean)
        return unique[:3]

    def _feature_cards_from_research(self, niche: str, topic: str, research: dict) -> list[dict]:
        tags = {
            "noticias": ["Titular", "Contexto", "Seguimiento"],
            "medio_ambiente": ["Problema", "Impacto", "Accion"],
            "deportes": ["Panorama", "Clave", "Seguimiento"],
            "tecnologia": ["Tema", "Uso", "Debate"],
            "cafeteria": ["Producto", "Atmosfera", "Conversion"],
            "corporativo": ["Propuesta", "Hallazgo", "Cierre"],
        }.get(niche, ["Principal", "Hallazgo", "Recurso"])
        references = research.get("references", [])
        points = research.get("key_points", [])
        cards = []
        for index, tag in enumerate(tags):
            point = points[index] if index < len(points) else {"title": f"{topic} {tag.lower()}", "description": f"Bloque editorial sobre {topic}."}
            reference = references[index] if index < len(references) else {}
            cards.append(
                self._card_from_research(
                    tag=tag,
                    title=self._sanitize_text(point.get("title", f"{topic} {tag.lower()}")),
                    description=self._sanitize_text(point.get("description", f"Contenido sobre {topic}.")),
                    source=reference.get("source") or point.get("source", "Base editorial"),
                    label=reference.get("label") or "Apoyo editorial",
                    snippet=reference.get("snippet") or point.get("description", ""),
                )
            )
        return cards

    def _experience_blocks_from_research(self, niche: str, topic: str, research: dict) -> list[dict]:
        points = research.get("key_points", [])
        blocks = []
        labels = {
            "noticias": ["Lectura rapida", "Contexto util", "Siguiente seguimiento"],
            "medio_ambiente": ["Problema aterrizado", "Impacto visible", "Accion concreta"],
            "deportes": ["Ritmo editorial", "Clave competitiva", "Comunidad y regreso"],
            "tecnologia": ["Utilidad real", "Cambio que propone", "Que conviene probar"],
            "cafeteria": ["Ambiente", "Producto", "Llamado a la accion"],
            "corporativo": ["Mensaje claro", "Prueba de valor", "Conversion"],
        }.get(niche, ["Panorama", "Profundiza", "Siguiente paso"])
        for index, label in enumerate(labels):
            point = points[index] if index < len(points) else {"description": f"Desarrolla {topic} desde un angulo distinto para sostener la lectura."}
            blocks.append({"title": label, "description": self._sanitize_text(point.get("description", ""))})
        return blocks

    def _itinerary_steps_from_research(self, niche: str, topic: str, research: dict) -> list[str]:
        points = research.get("key_points", [])
        base = [
            f"Abre con una entrada clara sobre {topic} y explica por que importa.",
            f"Desarrolla {points[0]['title'] if points else 'los hallazgos principales'} con ejemplos y apoyo visual.",
            f"Cierra con recursos, referencias y una accion para que el lector siga explorando {topic}.",
        ]
        if niche == "noticias":
            base[1] = "Sigue con contexto, cronologia, voces o datos que ayuden a interpretar la noticia."
        elif niche == "medio_ambiente":
            base[1] = "Profundiza en impactos, causas y soluciones que aterricen el problema en la vida real."
        elif niche == "deportes":
            base[1] = "Organiza protagonistas, calendario o momentos clave para que el lector siga el ritmo del tema."
        return base

    def _faq_items_from_research(self, data: dict, topic: str, research: dict) -> list[dict]:
        if data.get("faqs"):
            return self._build_faqs(data, topic)
        references = research.get("references", [])
        source_names = ", ".join(sorted({ref.get("source", "") for ref in references if ref.get("source")})) or "fuentes abiertas"
        base = self._build_faqs(data, topic)
        if base:
            base[0]["answer"] = self._sanitize_text(research.get("summary") or base[0]["answer"])
            if len(base) > 1:
                base[1]["answer"] = f"Este blog se apoya en {source_names} y organiza el tema con bloques, imagenes y lecturas complementarias."
        return base

    def _article_title_for_niche(self, niche: str, topic: str) -> str:
        if niche == "noticias":
            return f"Lectura desarrollada sobre {topic}"
        if niche == "medio_ambiente":
            return f"Contexto, impacto y acciones sobre {topic}"
        if niche == "deportes":
            return f"Panorama editorial de {topic}"
        return f"Contenido desarrollado sobre {topic}"

    def _article_intro_for_niche(self, niche: str, topic: str, summary: str) -> str:
        if niche == "medio_ambiente":
            return f"Esta lectura organiza {topic} en contexto, implicaciones y acciones utiles para el lector final."
        if niche == "noticias":
            return f"Esta seccion condensa {topic} en un formato editorial mas util que un simple titular."
        if niche == "deportes":
            return f"Esta seccion baja {topic} a ritmo competitivo, focos de lectura y seguimiento."
        return summary

    def _article_sections_from_research(self, niche: str, topic: str, research: dict) -> list[dict]:
        sections = []
        focus_phrase = research.get("focus_phrase", topic)
        for point in research.get("key_points", [])[:4]:
            title = self._sanitize_text(point.get("title", f"Clave sobre {topic}"))
            description = self._sanitize_text(point.get("description", ""))
            paragraphs = [description]
            related_refs = [
                ref for ref in research.get("references", [])
                if ref.get("title") and any(token in ref.get("title", "").lower() for token in title.lower().split()[:2])
            ]
            if related_refs:
                ref = related_refs[0]
                paragraphs.append(
                    f"Referencia relacionada: {self._sanitize_text(ref.get('title', 'Fuente de apoyo'))} desde {self._sanitize_text(ref.get('source', 'fuente externa'))}."
                )
            paragraphs.append(
                f"Este bloque se desarrollo pensando especificamente en {self._sanitize_text(focus_phrase)} para que el blog no se desvie hacia otro tema."
            )
            if niche == "medio_ambiente":
                paragraphs.append("Conviene aterrizar este punto con ejemplos locales, cifras o acciones concretas que el lector pueda reconocer.")
            elif niche == "noticias":
                paragraphs.append("Este frente funciona mejor si se acompana con cronologia, actores clave o datos que expliquen el contexto.")
            elif niche == "deportes":
                paragraphs.append("Aqui el blog puede sumar protagonistas, calendario, antecedentes o expectativa para volver la lectura mas viva.")
            sections.append({"title": title, "paragraphs": [p for p in paragraphs if p]})
        return sections[:3]

    def _sports_guide_sections(self, topic: str, research: dict, prompt: str) -> list[dict]:
        focus = self._sanitize_text(research.get("focus_phrase", topic))
        requested = self._sports_requested_angles(prompt)
        points = research.get("key_points", [])
        references = research.get("references", [])
        summary = self._sanitize_text(research.get("summary", ""))
        sections: list[dict] = []

        if "participantes" in requested:
            sections.append(
                {
                    "title": "Participantes y selecciones a seguir",
                    "paragraphs": [
                        summary or f"{focus} debe explicar primero quienes compiten y por que este cuadro de participantes importa.",
                        "Este bloque funciona mejor cuando resume favoritas, selecciones protagonistas y posibles sorpresas del torneo.",
                        self._support_reference_line(references, 0),
                    ],
                }
            )
        if "fechas" in requested or "calendario" in requested:
            sections.append(
                {
                    "title": "Calendario y fechas clave",
                    "paragraphs": [
                        "La guia debe marcar con claridad inauguracion, fase de grupos, cruces, semifinales y final para que el lector tenga una linea temporal util.",
                        "Si el prompt menciona fechas, esta seccion debe ser prioritaria y no quedarse en un comentario generico.",
                        self._support_reference_line(references, 1),
                    ],
                }
            )
        if "sedes" in requested or "paises" in requested:
            sections.append(
                {
                    "title": "Sedes, paises y contexto del torneo",
                    "paragraphs": [
                        f"Un blog de estilo guia sobre {focus} gana mucho valor cuando ordena ciudades sede, paises implicados o escenarios principales en una misma lectura.",
                        "Aqui conviene explicar por que ciertas sedes o contextos organizativos condicionan la experiencia del campeonato.",
                        self._support_reference_line(references, 2),
                    ],
                }
            )
        if "figuras" in requested or not sections:
            first_point = points[0]["description"] if points else f"{focus} necesita tambien una capa de protagonistas y contexto competitivo."
            sections.append(
                {
                    "title": "Protagonistas, favoritos y claves del torneo",
                    "paragraphs": [
                        self._sanitize_text(first_point),
                        "Esta parte debe ayudar al lector a entender quienes llegan mejor, que historias seguir y donde puede estar la tension deportiva.",
                        self._support_reference_line(references, 0),
                    ],
                }
            )
        unique_sections = []
        seen = set()
        for section in sections:
            title = section["title"]
            if title in seen:
                continue
            seen.add(title)
            unique_sections.append(
                {
                    "title": title,
                    "paragraphs": [paragraph for paragraph in section["paragraphs"] if paragraph and not paragraph.startswith("Referencia relacionada: None")],
                }
            )
        return unique_sections[:4]

    def _sports_requested_angles(self, prompt: str) -> set[str]:
        lowered = prompt.lower()
        angles = set()
        if any(word in lowered for word in ("paises", "países", "selecciones", "participantes", "equipos")):
            angles.add("participantes")
        if any(word in lowered for word in ("fechas", "calendario", "fixture", "partidos", "cronograma")):
            angles.add("fechas")
            angles.add("calendario")
        if any(word in lowered for word in ("sedes", "estadios", "ciudades", "pais anfitrion", "país anfitrión")):
            angles.add("sedes")
        if any(word in lowered for word in ("figuras", "favoritos", "estrellas", "protagonistas")):
            angles.add("figuras")
        if any(word in lowered for word in ("paises", "países")):
            angles.add("paises")
        return angles

    def _support_reference_line(self, references: list[dict], index: int) -> str:
        if index < len(references):
            ref = references[index]
            return f"Referencia relacionada: {self._sanitize_text(ref.get('title', 'Fuente de apoyo'))} desde {self._sanitize_text(ref.get('source', 'fuente externa'))}."
        return ""

    def _references_title_for_niche(self, niche: str) -> str:
        if niche == "noticias":
            return "Fuentes y lecturas para ampliar la cobertura"
        if niche == "medio_ambiente":
            return "Lecturas, fuentes y referencias del tema"
        if niche == "deportes":
            return "Cobertura, referencias y lecturas relacionadas"
        return "Referencias y articulos recomendados"

    def _references_intro_for_niche(self, niche: str, research: dict) -> str:
        note = research.get("citation_note", "")
        if note:
            return self._sanitize_text(note)
        if niche == "medio_ambiente":
            return "Usa estas referencias para profundizar cifras, conceptos y casos del tema."
        return "Estas referencias te ayudan a ampliar el blog con contexto, articulos y material de apoyo."

    def _card_from_research(self, tag: str, title: str, description: str, source: str, label: str, snippet: str) -> dict:
        snippet = self._sanitize_text(snippet)
        return {
            "tag": tag,
            "title": title,
            "description": description,
            "meta_1": f"Dato base: {description[:95]}",
            "meta_2": f"Fuente: {source}",
            "meta_3": f"Lectura de apoyo: {label}",
            "what_to_bring": f"Apoyate en {source} para reforzar este bloque.",
        }

    def _fallback_research(self, prompt: str, niche: str) -> dict:
        topic = self._extract_topic(prompt, niche)
        summary = self._default_promise_for_niche(niche, prompt)
        return {
            "summary": summary,
            "key_points": [
                {"title": f"Contexto de {topic}", "description": summary, "source": "Base editorial"},
                {"title": "Hallazgos principales", "description": f"Organiza {topic} en bloques con ejemplos, datos y apoyo visual.", "source": "Base editorial"},
                {"title": "Recursos para seguir", "description": f"Cierra el blog con referencias y contenido derivado sobre {topic}.", "source": "Base editorial"},
            ],
            "references": [
                {"title": f"Guia base sobre {topic}", "url": "", "source": "Base editorial", "snippet": summary, "label": "Referencia inicial"}
            ],
            "image_query": self._build_image_query(niche, topic),
            "citation_note": "No hubo investigacion externa disponible en este momento; conviene revisar y enriquecer antes de publicar.",
        }

    def _sports_event_feature_cards(self, topic: str, research: dict | None = None, prompt: str = "") -> list[dict]:
        if not research:
            return [
                self._card("Selecciones", "Paises clasificados y favoritos", f"Organiza el bloque principal de {topic} con selecciones confirmadas, candidatas y focos de conversacion."),
                self._card("Calendario", "Fechas y partidos clave", "Resume inauguracion, jornadas decisivas, cruces esperados y momentos que marcaran la cobertura."),
                self._card("Sedes", "Ciudades, estadios y ambiente", "Da contexto sobre donde se juega, que escenarios pesan y como cambia la experiencia del torneo."),
            ]
        requested = self._sports_requested_angles(prompt)
        cards = []
        mapping = [
            ("Selecciones", "Paises y participantes", "participantes"),
            ("Calendario", "Fechas y momentos clave", "fechas"),
            ("Sedes", "Sedes, estadios y contexto", "sedes"),
            ("Figuras", "Protagonistas y favoritos", "figuras"),
        ]
        references = research.get("references", [])
        for index, (tag, title, key) in enumerate(mapping):
            if requested and key not in requested and not (key == "participantes" and "paises" in requested):
                continue
            snippet = self._sanitize_text(references[index]["snippet"]) if index < len(references) else f"Bloque editorial orientado a {title.lower()}."
            source = references[index].get("source", "Base editorial") if index < len(references) else "Base editorial"
            cards.append(
                self._card_from_research(
                    tag=tag,
                    title=title,
                    description=snippet[:160] or f"Resumen de {title.lower()} para {topic}.",
                    source=source,
                    label="Guia del torneo",
                    snippet=snippet,
                )
            )
        return cards[:4] or self._feature_cards_from_research("deportes", topic, research)

    def _sports_event_experience_blocks(self, topic: str, research: dict | None = None, prompt: str = "") -> list[dict]:
        if not research:
            return [
                {"title": "Fixture claro", "description": f"Haz que {topic} se entienda rapido con fechas, cruces y lectura escaneable."},
                {"title": "Narrativa de selecciones", "description": "Combina favoritas, sorpresas, grupos y figuras para que la pagina tenga tension."},
                {"title": "Cobertura viva", "description": "Deja espacio a actualizaciones, resultados, analisis y seguimiento durante el torneo."},
            ]
        angles = self._sports_requested_angles(prompt)
        blocks = []
        if "fechas" in angles or "calendario" in angles:
            blocks.append({"title": "Calendario util", "description": "La experiencia debe permitir ubicar rapido las fechas grandes y los tramos decisivos del torneo."})
        if "participantes" in angles or "paises" in angles:
            blocks.append({"title": "Selecciones claras", "description": "El lector debe identificar facilmente quienes compiten, cuales son favoritas y por que destacan."})
        if "sedes" in angles:
            blocks.append({"title": "Mapa del evento", "description": "Sedes y escenarios deben sentirse parte de la historia, no un dato perdido al final."})
        blocks.append({"title": "Cobertura viva", "description": "La guia debe dejar espacio a resumen, contexto y seguimiento para que el blog se sienta actualizado."})
        return blocks[:4]

    def _sports_event_itinerary(self, topic: str, research: dict | None, prompt: str) -> list[str]:
        angles = self._sports_requested_angles(prompt)
        steps = [f"Abre con un panorama claro de {topic} y explica por que esta edicion importa."]
        if "participantes" in angles or "paises" in angles:
            steps.append("Sigue con las selecciones o paises participantes y marca favoritos, sorpresas y cruces atractivos.")
        if "fechas" in angles or "calendario" in angles:
            steps.append("Ordena luego las fechas clave, fases del torneo y momentos que el lector no deberia perderse.")
        if "sedes" in angles:
            steps.append("Añade las sedes principales, contexto de estadios o ciudades y su peso dentro del evento.")
        steps.append("Cierra con protagonistas, referencias y una llamada a seguir la cobertura.")
        return steps[:4]

    def _sports_event_faqs(self, topic: str, research: dict | None = None, prompt: str = "") -> list[dict]:
        if not research:
            return [
                {"question": f"Como estructurar un blog sobre {topic} para que no se vea vacio?", "answer": "Separando panorama, selecciones, fechas, sedes y favoritos en bloques con jerarquia clara."},
                {"question": "Que informacion debe ir primero en un especial deportivo?", "answer": "Lo mas util para el lector: que torneo es, cuando arranca, quienes participan y que partidos generan expectativa."},
                {"question": "Las imagenes del torneo se pueden reutilizar libremente?", "answer": "No siempre. Revisa fuente, autor y licencia antes de publicar o reutilizar visuales del evento."},
            ]
        base = self._faq_items_from_research({"niche": "deportes", "faqs": []}, topic, research)
        angles = self._sports_requested_angles(prompt)
        if angles and len(base) > 1:
            ordered = ", ".join(sorted(angles))
            base[1]["answer"] = f"En este caso conviene priorizar {ordered} porque eso fue lo que pidió el prompt y es lo que el lector espera encontrar primero."
        return base

    def _card(self, tag: str, title: str, description: str) -> dict:
        return {
            "tag": tag,
            "title": title,
            "description": description,
            "meta_1": "Clave principal: bloque ajustable al tema solicitado.",
            "meta_2": "Formato sugerido: titulo, bajada y un dato o ejemplo potente.",
            "meta_3": "Uso recomendado: seccion escaneable para enganchar lectura.",
            "tips": "Puedes editar este punto en tiempo real desde el panel.",
            "what_to_bring": "Recurso recomendado: agrega fuente, ejemplo o CTA segun el nicho.",
            "note": "Bloque generado segun el nicho solicitado, no como plantilla de turismo.",
        }

    def _plain_attribution(self, image: dict) -> str:
        author = image.get("author") or "Autor no especificado"
        source = image.get("source") or "Fuente externa"
        license_name = image.get("license") or "Licencia no indicada"
        return f"Credito visual: {author} | {source} | {license_name}"

    def _generic_detail_labels(self, niche: str) -> dict:
        if niche == "noticias":
            return {
                "fact_1": "Angulo editorial",
                "fact_2": "Formato sugerido",
                "fact_3": "Uso recomendado",
                "fact_4": "Recurso recomendado",
                "fact_5": "Consejo de portada",
                "fact_6": "Nota editorial",
            }
        if niche == "medio_ambiente":
            return {
                "fact_1": "Problema central",
                "fact_2": "Enfoque sugerido",
                "fact_3": "Uso recomendado",
                "fact_4": "Accion sugerida",
                "fact_5": "Consejo de enfoque",
                "fact_6": "Nota de contexto",
            }
        if niche == "deportes":
            return {
                "fact_1": "Seccion principal",
                "fact_2": "Formato sugerido",
                "fact_3": "Ritmo recomendado",
                "fact_4": "Recurso recomendado",
                "fact_5": "Consejo de engagement",
                "fact_6": "Nota de cobertura",
            }
        if niche == "tecnologia":
            return {
                "fact_1": "Problema o foco",
                "fact_2": "Formato sugerido",
                "fact_3": "Uso recomendado",
                "fact_4": "Recurso o demo",
                "fact_5": "Consejo UX",
                "fact_6": "Nota tecnica",
            }
        if niche == "cafeteria":
            return {
                "fact_1": "Bloque principal",
                "fact_2": "Formato sugerido",
                "fact_3": "Uso recomendado",
                "fact_4": "Producto o recurso",
                "fact_5": "Consejo visual",
                "fact_6": "Nota comercial",
            }
        return {
            "fact_1": "Clave principal",
            "fact_2": "Formato sugerido",
            "fact_3": "Uso recomendado",
            "fact_4": "Recurso recomendado",
            "fact_5": "Consejo rapido",
            "fact_6": "Nota importante",
        }

    def _apply_instruction(self, content: dict, instruction: str, blog: Blog) -> dict:
        updated = {**content}
        lowered = instruction.lower()
        if "whatsapp" in lowered:
            updated["contact_copy"] = (
                updated.get("contact_copy", "")
                + " Tambien puedes conectar este bloque con un boton directo de WhatsApp para consultas rapidas."
            ).strip()
        if "precio" in lowered or "tarifa" in lowered:
            if blog.niche == "turismo":
                updated["experience_blocks"] = updated.get("experience_blocks", []) + [
                    {
                        "title": "Planes sugeridos",
                        "description": "Incluye aqui precios referenciales por experiencia, transporte o paquetes combinados.",
                    }
                ]
            else:
                updated["experience_blocks"] = updated.get("experience_blocks", []) + [
                    {
                        "title": "Oferta o recurso destacado",
                        "description": "Incluye aqui una propuesta comercial, recurso descargable o bloque de valor acorde al tema.",
                    }
                ]
        if "verde" in lowered:
            blog.palette = {"primary": "#166534", "secondary": "#84cc16", "background": "#f7fee7", "text": "#14532d"}
        if "galeria" in lowered or "imagen" in lowered:
            updated["credits_note"] = (
                "Galeria reforzada con imagenes de referencia. Mantuvimos visible la autoria y la licencia para evitar uso sin atribucion."
            )
        if "contacto" in lowered:
            updated["contact_copy"] = self._contact_copy_for_niche(blog.niche)
        if "horario" in lowered or "precio" in lowered or "info" in lowered:
            updated["practical_note"] = (
                "Se reforzo la capa informativa con datos de apoyo acordes al nicho para que el blog se sienta mas util y accionable."
                if blog.niche != "turismo"
                else "Se reforzo la capa informativa con horarios y precios referenciales para que el blog se sienta mas util y accionable."
            )
        return updated

    def _build_version(self, blog: Blog, change_summary: str, version_number: int) -> BlogVersion:
        palette = blog.palette or {"primary": "#14532d", "secondary": "#f59e0b", "background": "#f8fafc", "text": "#0f172a"}
        html_content = self._render_html(blog)
        css_content = self._render_css(palette)
        js_content = self._render_js(blog)
        return BlogVersion(
            blog_id=blog.id,
            version_number=version_number,
            change_summary=change_summary,
            html_content=html_content,
            css_content=css_content,
            js_content=js_content,
            seo_metadata={
                "title": blog.title,
                "description": blog.metadata_json.get("seo_description", ""),
                "open_graph_title": blog.title,
                "open_graph_description": blog.metadata_json.get("seo_description", ""),
            },
            generation_prompt=change_summary,
            embedding=self.vector.embed_text(f"{blog.title}\n{change_summary}"),
        )

    def _render_html(self, blog: Blog) -> str:
        content = blog.metadata_json.get("content") or self._build_content_blueprint(
            blog.brief,
            {
                "title": blog.title,
                "niche": blog.niche,
                "target_audience": blog.target_audience,
                "tone": "cercano, inspirador y confiable",
                "promise": blog.metadata_json.get("seo_description", blog.brief),
                "primary_cta": self._default_cta_for_niche(blog.niche),
                "faqs": [],
            },
            blog.metadata_json.get("places", []),
        )
        images = blog.metadata_json.get("images", [])
        destinations_html = self._render_feature_cards(blog.niche, content)
        experiences_html = "".join(
            f"""
            <article class="experience-card">
              <h3>{escape(item['title'])}</h3>
              <p>{escape(item['description'])}</p>
            </article>
            """
            for item in content.get("experience_blocks", [])
        )
        itinerary_html = "".join(
            f"<li>{escape(step)}</li>" for step in content.get("itinerary_steps", [])
        )
        faq_html = "".join(
            f"""
            <details class="faq-item">
              <summary>{escape(item['question'])}</summary>
              <p>{escape(item['answer'])}</p>
            </details>
            """
            for item in content.get("faq_items", [])
        )
        gallery_html = self._render_gallery(images)
        about_html = "".join(f"<p>{escape(paragraph)}</p>" for paragraph in content.get("about_paragraphs", []))
        hero_html = self._render_hero(blog, content)
        main_layout = self._render_layout_by_niche(blog, content, about_html, destinations_html, experiences_html, itinerary_html, gallery_html, faq_html)
        return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(blog.title)}</title>
  <meta name="description" content="{escape(blog.metadata_json.get('seo_description', ''))}">
  <meta property="og:title" content="{escape(blog.title)}">
  <meta property="og:description" content="{escape(blog.metadata_json.get('seo_description', ''))}">
  <link rel="stylesheet" href="/generated/{blog.slug}/style.css">
</head>
<body class="theme-{blog.niche} variant-{escape(content.get('style_variant', 'atlas'))}">
  <header class="hero hero-{escape(self._hero_mode_for_variant(content.get('style_variant', 'atlas')))}">
    <nav class="topbar">
      <div class="brand">{escape(blog.title)}</div>
      <div class="menu">
        <a href="#about">{escape(content.get('nav_primary', 'Sobre'))}</a>
        <a href="#destinations">{escape(content.get('nav_secondary', 'Destinos'))}</a>
        <a href="#gallery">{escape(content.get('nav_tertiary', 'Galeria'))}</a>
        <a href="#references">{escape(content.get('nav_quaternary', 'Referencias'))}</a>
        <a href="#faq">FAQ</a>
        <a href="#contact">Contacto</a>
      </div>
    </nav>
    {hero_html}
  </header>
  <main>{main_layout}</main>
  <footer class="footer">
    <p>{escape(blog.title)} | Generado por BlogBot IA</p>
    <p class="footer-note">Usa este blog como base editable y valida cualquier dato sensible antes de publicarlo.</p>
  </footer>
  <script src="/generated/{blog.slug}/app.js"></script>
</body>
</html>"""

    def _render_layout_by_niche(
        self,
        blog: Blog,
        content: dict,
        about_html: str,
        destinations_html: str,
        experiences_html: str,
        itinerary_html: str,
        gallery_html: str,
        faq_html: str,
    ) -> str:
        variant = content.get("style_variant", "atlas")
        if blog.niche == "noticias":
            return self._render_news_layout(content, about_html, destinations_html, experiences_html, gallery_html, faq_html, variant)
        if blog.niche == "medio_ambiente":
            return self._render_environment_layout(content, about_html, destinations_html, experiences_html, itinerary_html, gallery_html, faq_html, variant)
        if blog.niche == "deportes":
            return self._render_sports_layout(content, about_html, destinations_html, experiences_html, itinerary_html, gallery_html, faq_html, variant)
        return self._render_default_layout(content, about_html, destinations_html, experiences_html, itinerary_html, gallery_html, faq_html, variant)

    def _template_family_for_variant(self, variant: str) -> str:
        if variant in {"magazine", "feature", "postcard", "salon", "roastery"}:
            return "magazine"
        if variant in {"briefing", "bulletin", "sidebar", "fieldnote", "journal", "ledger", "boardroom"}:
            return "sidebar"
        if variant in {"manifesto", "campaign", "poster", "duel"}:
            return "poster"
        if variant in {"landing", "showcase", "catalog", "menu", "grid", "lab"}:
            return "showcase"
        return "split"

    def _hero_mode_for_variant(self, variant: str) -> str:
        mapping = {
            "postcard": "magazine",
            "souvenir": "magazine",
            "magazine": "magazine",
            "feature": "magazine",
            "roastery": "magazine",
            "salon": "magazine",
            "briefing": "sidebar",
            "bulletin": "sidebar",
            "sidebar": "sidebar",
            "fieldnote": "sidebar",
            "journal": "sidebar",
            "ledger": "sidebar",
            "boardroom": "sidebar",
            "manifesto": "poster",
            "campaign": "poster",
            "poster": "poster",
            "duel": "poster",
            "expedition": "poster",
            "trail": "poster",
            "landing": "showcase",
            "grid": "showcase",
            "showcase": "showcase",
            "catalog": "showcase",
            "menu": "showcase",
            "brew": "showcase",
        }
        return mapping.get(variant, "split")

    def _render_hero(self, blog: Blog, content: dict) -> str:
        mode = self._hero_mode_for_variant(content.get("style_variant", "atlas"))
        summary_items = [
            ("Publico", blog.target_audience),
            ("Estilo", blog.design_style),
            ("Estado", blog.status),
            ("Tema", content.get("location", "Tema principal")),
        ]
        chips = "".join(
            f'<span class="hero-chip"><strong>{escape(label)}:</strong> {escape(value)}</span>'
            for label, value in summary_items
        )
        rail = "".join(
            f"<li><strong>{escape(label)}:</strong> {escape(value)}</li>"
            for label, value in summary_items
        )
        actions = f"""
        <div class="hero-actions">
          <a class="cta" href="#contact">{escape(content.get('cta_label', 'Explorar blog'))}</a>
          <a class="secondary-link" href="#gallery">Ver galeria</a>
        </div>
        """
        if mode == "poster":
            return f"""
    <div class="hero-poster-shell">
      <p class="eyebrow">{escape(content.get('hero_kicker', blog.niche.upper()))}</p>
      <h1>{escape(content.get('hero_headline', blog.title))}</h1>
      <p class="hero-lead centered">{escape(content.get('hero_intro', ''))}</p>
      <p class="hero-support centered">{escape(content.get('hero_support', ''))}</p>
      <div class="hero-chip-row">{chips}</div>
      {actions}
    </div>
            """
        if mode == "sidebar":
            return f"""
    <div class="hero-grid sidebar-hero">
      <div class="hero-copy">
        <p class="eyebrow">{escape(content.get('hero_kicker', blog.niche.upper()))}</p>
        <h1>{escape(content.get('hero_headline', blog.title))}</h1>
        <p class="hero-lead">{escape(content.get('hero_intro', ''))}</p>
        <p class="hero-support">{escape(content.get('hero_support', ''))}</p>
        <div class="hero-chip-row compact">{chips}</div>
        {actions}
      </div>
      <aside class="hero-panel hero-panel-rail">
        <h2>Mapa rapido</h2>
        <ul>{rail}</ul>
      </aside>
    </div>
            """
        if mode == "magazine":
            return f"""
    <div class="hero-grid magazine-hero">
      <div class="hero-copy framed">
        <p class="eyebrow">{escape(content.get('hero_kicker', blog.niche.upper()))}</p>
        <h1>{escape(content.get('hero_headline', blog.title))}</h1>
        <p class="hero-lead">{escape(content.get('hero_intro', ''))}</p>
        {actions}
      </div>
      <aside class="hero-panel magazine-panel">
        <p class="eyebrow">Edicion visual</p>
        <p class="hero-support">{escape(content.get('hero_support', ''))}</p>
        <div class="hero-chip-row">{chips}</div>
      </aside>
    </div>
            """
        if mode == "showcase":
            return f"""
    <div class="hero-grid showcase-hero">
      <div class="hero-copy">
        <p class="eyebrow">{escape(content.get('hero_kicker', blog.niche.upper()))}</p>
        <h1>{escape(content.get('hero_headline', blog.title))}</h1>
      </div>
      <div class="hero-showcase-stack">
        <article class="hero-panel accent-panel">
          <p class="hero-lead">{escape(content.get('hero_intro', ''))}</p>
        </article>
        <article class="hero-panel">
          <p class="hero-support">{escape(content.get('hero_support', ''))}</p>
          <div class="hero-chip-row compact">{chips}</div>
          {actions}
        </article>
      </div>
    </div>
            """
        return f"""
    <div class="hero-grid">
      <div class="hero-copy">
        <p class="eyebrow">{escape(content.get('hero_kicker', blog.niche.upper()))}</p>
        <h1>{escape(content.get('hero_headline', blog.title))}</h1>
        <p class="hero-lead">{escape(content.get('hero_intro', ''))}</p>
        <p class="hero-support">{escape(content.get('hero_support', ''))}</p>
        {actions}
      </div>
      <aside class="hero-panel">
        <h2>Resumen rapido</h2>
        <ul>{rail}</ul>
      </aside>
    </div>
        """

    def _render_default_layout(
        self,
        content: dict,
        about_html: str,
        destinations_html: str,
        experiences_html: str,
        itinerary_html: str,
        gallery_html: str,
        faq_html: str,
        variant: str,
    ) -> str:
        family = content.get("template_family", self._template_family_for_variant(variant))
        if content.get("nav_secondary") == "Secciones":
            return self._render_generic_topic_layout(content, about_html, destinations_html, experiences_html, itinerary_html, gallery_html, faq_html, family)
        if variant in {"postcard", "souvenir", "catalog"}:
            return f"""
    <section id="about" class="section postcard-shell">
      <article class="postcard-stamp">
        <p class="section-kicker">Inspiracion</p>
        <h2>{escape(content.get('about_title', 'Sobre este destino'))}</h2>
        <div class="story-copy compact">{about_html}</div>
      </article>
      <article class="postcard-note">
        <p class="section-kicker">{escape(content.get('section_kicker', 'Lugares destacados'))}</p>
        <p>{escape(content.get('practical_note', ''))}</p>
      </article>
    </section>

    <section id="destinations" class="section">
      <div class="section-head">
        <div>
          <p class="section-kicker">{escape(content.get('section_kicker', 'Lugares destacados'))}</p>
          <h2>{escape(content.get('section_title', 'Experiencias que vale la pena mostrar'))}</h2>
        </div>
      </div>
      <div class="postcard-grid">{destinations_html}</div>
    </section>
    {self._render_gallery_section(content, gallery_html)}
    <section class="section split-layout">
      <div class="itinerary-card">
        <p class="section-kicker">Ruta sugerida</p>
        <h3>{escape(content.get('itinerary_title', 'Ruta recomendada'))}</h3>
        <ol>{itinerary_html}</ol>
      </div>
      <div class="experience-grid">{experiences_html}</div>
    </section>
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
            """
        if variant in {"expedition", "trail", "showcase", "landing"}:
            return f"""
    <section id="about" class="section expedition-intro">
      <div class="expedition-lead">
        <p class="section-kicker">Bitacora</p>
        <h2>{escape(content.get('about_title', 'Sobre este destino'))}</h2>
        <div class="story-copy compact">{about_html}</div>
      </div>
      <div class="itinerary-card">
        <p class="section-kicker">Plan base</p>
        <h3>{escape(content.get('itinerary_title', 'Ruta recomendada'))}</h3>
        <ol>{itinerary_html}</ol>
      </div>
    </section>
    <section id="destinations" class="section">
      <div class="destination-grid">{destinations_html}</div>
    </section>
    <section class="section expedition-sidecar">
      <div>
        <p class="section-kicker">{escape(content.get('experience_kicker', 'Que encuentra el visitante'))}</p>
        <h2>{escape(content.get('experience_title', 'Contenido listo para inspirar y convertir'))}</h2>
      </div>
      <div class="experience-grid">{experiences_html}</div>
    </section>
    {self._render_gallery_section(content, gallery_html)}
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
            """
        return f"""
    <section id="about" class="section story-grid">
      <div>
        <p class="section-kicker">Vision editorial</p>
        <h2>{escape(content.get('about_title', 'Sobre este destino'))}</h2>
      </div>
      <div class="story-copy">{about_html}</div>
    </section>

    <section id="destinations" class="section">
      <div class="section-head">
        <div>
          <p class="section-kicker">{escape(content.get('section_kicker', 'Lugares destacados'))}</p>
          <h2>{escape(content.get('section_title', 'Experiencias que vale la pena mostrar'))}</h2>
        </div>
        <p class="section-note">{escape(content.get('section_note', content.get('practical_note', '')))}</p>
      </div>
      <div class="destination-grid">{destinations_html}</div>
    </section>

    <section class="section split-layout">
      <div>
        <p class="section-kicker">{escape(content.get('experience_kicker', 'Que encuentra el visitante'))}</p>
        <h2>{escape(content.get('experience_title', 'Contenido listo para inspirar y convertir'))}</h2>
        <div class="experience-grid">{experiences_html}</div>
      </div>
      <div class="itinerary-card">
        <p class="section-kicker">Ruta sugerida</p>
        <h3>{escape(content.get('itinerary_title', 'Ruta recomendada'))}</h3>
        <ol>{itinerary_html}</ol>
      </div>
    </section>

    {self._render_gallery_section(content, gallery_html)}
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
        """

    def _render_generic_topic_layout(
        self,
        content: dict,
        about_html: str,
        destinations_html: str,
        experiences_html: str,
        itinerary_html: str,
        gallery_html: str,
        faq_html: str,
        family: str,
    ) -> str:
        if family == "poster":
            return f"""
    <section id="about" class="section manifesto-shell">
      <article class="impact-panel">
        <p class="section-kicker">Tema central</p>
        <h2>{escape(content.get('about_title', 'Tema principal'))}</h2>
        <p>{escape(content.get('practical_note', ''))}</p>
      </article>
      <article class="eco-statement">
        <div class="story-copy compact">{about_html}</div>
      </article>
    </section>
    <section id="destinations" class="section">
      <div class="destination-grid">{destinations_html}</div>
    </section>
    <section class="section eco-split">
      <div class="experience-grid">{experiences_html}</div>
      <div class="itinerary-card wide">
        <p class="section-kicker">Recorrido editorial</p>
        <ol>{itinerary_html}</ol>
      </div>
    </section>
    {self._render_gallery_section(content, gallery_html)}
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
            """
        if family == "sidebar":
            return f"""
    <section id="about" class="section newsroom-shell">
      <article class="headline-card">
        <p class="section-kicker">Contexto</p>
        <h2>{escape(content.get('about_title', 'Tema principal'))}</h2>
        <div class="story-copy compact">{about_html}</div>
      </article>
      <article class="headline-card accent">
        <p class="section-kicker">Clave del enfoque</p>
        <h3>{escape(content.get('hero_headline', 'Tema principal'))}</h3>
        <p>{escape(content.get('practical_note', ''))}</p>
      </article>
    </section>
    <section id="destinations" class="section">
      <div class="news-grid">
        <div class="news-lead-stack">{destinations_html}</div>
        <aside class="news-side-rail">
          <div class="rail-card">
            <p class="section-kicker">Secuencia</p>
            <h3>{escape(content.get('itinerary_title', 'Secuencia recomendada'))}</h3>
            <ol>{itinerary_html}</ol>
          </div>
          <div class="rail-card">
            <p class="section-kicker">{escape(content.get('experience_kicker', 'Experiencia'))}</p>
            <div class="experience-grid">{experiences_html}</div>
          </div>
        </aside>
      </div>
    </section>
    {self._render_gallery_section(content, gallery_html)}
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
            """
        if family == "showcase":
            return f"""
    <section id="about" class="section fieldnote-shell">
      <div class="fieldnote-log">
        <p class="section-kicker">Concepto</p>
        <h2>{escape(content.get('about_title', 'Tema principal'))}</h2>
        <div class="story-copy compact">{about_html}</div>
      </div>
      <div class="fieldnote-actions">
        <div class="experience-grid">{experiences_html}</div>
      </div>
    </section>
    <section id="destinations" class="section">
      <div class="magazine-columns">{destinations_html}</div>
    </section>
    {self._render_gallery_section(content, gallery_html)}
    <section class="section">
      <div class="itinerary-card wide">
        <p class="section-kicker">{escape(content.get('itinerary_title', 'Secuencia sugerida'))}</p>
        <ol>{itinerary_html}</ol>
      </div>
    </section>
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
            """
        if family == "magazine":
            return f"""
    <section id="about" class="section magazine-lead">
      <article class="magazine-cover">
        <p class="section-kicker">Apertura</p>
        <h2>{escape(content.get('hero_headline', 'Tema principal'))}</h2>
        <div class="story-copy compact">{about_html}</div>
      </article>
      <article class="magazine-sidebar">
        <p class="section-kicker">{escape(content.get('experience_kicker', 'Experiencia'))}</p>
        <div class="experience-grid">{experiences_html}</div>
      </article>
    </section>
    <section id="destinations" class="section">
      <div class="magazine-columns">{destinations_html}</div>
    </section>
    {self._render_gallery_section(content, gallery_html)}
    <section class="section split-layout">
      <div class="itinerary-card">
        <p class="section-kicker">{escape(content.get('itinerary_title', 'Secuencia sugerida'))}</p>
        <ol>{itinerary_html}</ol>
      </div>
      <div class="impact-panel">
        <p class="section-kicker">Nota de cierre</p>
        <p>{escape(content.get('practical_note', ''))}</p>
      </div>
    </section>
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
            """
        return f"""
    <section id="about" class="section story-grid">
      <div>
        <p class="section-kicker">Vision del blog</p>
        <h2>{escape(content.get('about_title', 'Tema principal'))}</h2>
      </div>
      <div class="story-copy">{about_html}</div>
    </section>
    <section id="destinations" class="section">
      <div class="section-head">
        <div>
          <p class="section-kicker">{escape(content.get('section_kicker', 'Secciones clave'))}</p>
          <h2>{escape(content.get('section_title', 'Bloques principales'))}</h2>
        </div>
        <p class="section-note">{escape(content.get('section_note', ''))}</p>
      </div>
      <div class="destination-grid">{destinations_html}</div>
    </section>
    <section class="section split-layout">
      <div>
        <p class="section-kicker">{escape(content.get('experience_kicker', 'Experiencia del sitio'))}</p>
        <h2>{escape(content.get('experience_title', 'Contenido listo para publicar'))}</h2>
        <div class="experience-grid">{experiences_html}</div>
      </div>
      <div class="itinerary-card">
        <p class="section-kicker">{escape(content.get('itinerary_title', 'Secuencia sugerida'))}</p>
        <ol>{itinerary_html}</ol>
      </div>
    </section>
    {self._render_gallery_section(content, gallery_html)}
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
        """

    def _render_news_layout(self, content: dict, about_html: str, destinations_html: str, experiences_html: str, gallery_html: str, faq_html: str, variant: str) -> str:
        if variant in {"magazine", "feature", "poster"}:
            return f"""
    <section id="about" class="section magazine-lead">
      <article class="magazine-cover">
        <p class="section-kicker">Edicion principal</p>
        <h2>{escape(content.get('hero_headline', 'Titular principal'))}</h2>
        <div class="story-copy compact">{about_html}</div>
      </article>
      <article class="magazine-sidebar">
        <p class="section-kicker">En esta edicion</p>
        <div class="experience-grid">{experiences_html}</div>
      </article>
    </section>
    <section id="destinations" class="section">
      <div class="magazine-columns">{destinations_html}</div>
    </section>
    {self._render_gallery_section(content, gallery_html)}
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
            """
        if variant in {"briefing", "bulletin", "sidebar"}:
            return f"""
    <section id="about" class="section briefing-strip">
      <div class="briefing-main">
        <p class="section-kicker">Briefing</p>
        <h2>{escape(content.get('about_title', 'Linea editorial'))}</h2>
        <div class="story-copy compact">{about_html}</div>
      </div>
      <div class="briefing-list">
        <div class="rail-card">
          <p class="section-kicker">Ruta editorial</p>
          <ol>{''.join(f'<li>{escape(step)}</li>' for step in content.get('itinerary_steps', []))}</ol>
        </div>
      </div>
    </section>
    <section id="destinations" class="section">
      <div class="news-lead-stack">{destinations_html}</div>
    </section>
    <section class="section">
      <div class="experience-grid">{experiences_html}</div>
    </section>
    {self._render_gallery_section(content, gallery_html)}
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
            """
        return f"""
    <section id="about" class="section newsroom-shell">
      <article class="headline-card">
        <p class="section-kicker">Editorial</p>
        <h2>{escape(content.get('about_title', 'Linea editorial'))}</h2>
        <div class="story-copy compact">{about_html}</div>
      </article>
      <article class="headline-card accent">
        <p class="section-kicker">Clave de portada</p>
        <h3>{escape(content.get('hero_headline', 'Titular principal'))}</h3>
        <p>{escape(content.get('practical_note', ''))}</p>
      </article>
    </section>

    <section id="destinations" class="section">
      <div class="section-head">
        <div>
          <p class="section-kicker">{escape(content.get('section_kicker', 'Cobertura principal'))}</p>
          <h2>{escape(content.get('section_title', 'Temas y frentes informativos'))}</h2>
        </div>
        <p class="section-note">{escape(content.get('section_note', ''))}</p>
      </div>
      <div class="news-grid">
        <div class="news-lead-stack">{destinations_html}</div>
        <aside class="news-side-rail">
          <div class="rail-card">
            <p class="section-kicker">Seguimiento</p>
            <h3>{escape(content.get('itinerary_title', 'Ruta editorial'))}</h3>
            <ol>{''.join(f'<li>{escape(step)}</li>' for step in content.get('itinerary_steps', []))}</ol>
          </div>
          <div class="rail-card">
            <p class="section-kicker">{escape(content.get('experience_kicker', 'Propuesta de valor'))}</p>
            <div class="experience-grid">{experiences_html}</div>
          </div>
        </aside>
      </div>
    </section>

    {self._render_gallery_section(content, gallery_html)}
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
        """

    def _render_environment_layout(self, content: dict, about_html: str, destinations_html: str, experiences_html: str, itinerary_html: str, gallery_html: str, faq_html: str, variant: str) -> str:
        if variant in {"fieldnote", "journal", "sidebar"}:
            return f"""
    <section id="about" class="section fieldnote-shell">
      <div class="fieldnote-log">
        <p class="section-kicker">Cuaderno de campo</p>
        <h2>{escape(content.get('about_title', 'Por que importa este tema'))}</h2>
        <div class="story-copy compact">{about_html}</div>
      </div>
      <div class="fieldnote-actions">
        <div class="experience-grid">{experiences_html}</div>
      </div>
    </section>
    <section id="destinations" class="section">
      <div class="eco-card-ribbon">{destinations_html}</div>
    </section>
    {self._render_gallery_section(content, gallery_html)}
    <section class="section">
      <div class="itinerary-card wide">
        <p class="section-kicker">Ruta de accion</p>
        <ol>{itinerary_html}</ol>
      </div>
    </section>
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
            """
        if variant in {"manifesto", "campaign", "poster"}:
            return f"""
    <section id="about" class="section manifesto-shell">
      <article class="impact-panel">
        <p class="section-kicker">Manifiesto</p>
        <h2>{escape(content.get('hero_headline', 'Tema central'))}</h2>
        <p>{escape(content.get('hero_intro', ''))}</p>
      </article>
      <article class="eco-statement">
        <div class="story-copy compact">{about_html}</div>
      </article>
    </section>
    <section id="destinations" class="section">
      <div class="destination-grid">{destinations_html}</div>
    </section>
    <section class="section eco-split">
      <div class="experience-grid">{experiences_html}</div>
      <div class="itinerary-card wide"><ol>{itinerary_html}</ol></div>
    </section>
    {self._render_gallery_section(content, gallery_html)}
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
            """
        return f"""
    <section id="about" class="section eco-intro">
      <div class="eco-statement">
        <p class="section-kicker">Contexto</p>
        <h2>{escape(content.get('about_title', 'Por que importa este tema'))}</h2>
        <div class="story-copy compact">{about_html}</div>
      </div>
      <div class="eco-checklist">
        <p class="section-kicker">Lectura accionable</p>
        <h3>{escape(content.get('experience_title', 'Contenido que informa y moviliza'))}</h3>
        <div class="experience-grid">{experiences_html}</div>
      </div>
    </section>

    <section id="destinations" class="section">
      <div class="section-head">
        <div>
          <p class="section-kicker">{escape(content.get('section_kicker', 'Temas clave'))}</p>
          <h2>{escape(content.get('section_title', 'Frentes editoriales sobre medio ambiente'))}</h2>
        </div>
        <p class="section-note">{escape(content.get('section_note', ''))}</p>
      </div>
      <div class="eco-card-ribbon">{destinations_html}</div>
    </section>

    <section class="section split-layout eco-split">
      <div class="itinerary-card wide">
        <p class="section-kicker">Ruta de lectura</p>
        <h3>{escape(content.get('itinerary_title', 'Ruta de lectura recomendada'))}</h3>
        <ol>{itinerary_html}</ol>
      </div>
      <div class="impact-panel">
        <p class="section-kicker">Nota importante</p>
        <p>{escape(content.get('practical_note', ''))}</p>
      </div>
    </section>

    {self._render_gallery_section(content, gallery_html)}
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
        """

    def _render_sports_layout(self, content: dict, about_html: str, destinations_html: str, experiences_html: str, itinerary_html: str, gallery_html: str, faq_html: str, variant: str) -> str:
        if variant in {"arena", "duel", "poster"}:
            return f"""
    <section id="about" class="section arena-shell">
      <div class="scoreboard-main">
        <p class="section-kicker">Arena</p>
        <h2>{escape(content.get('about_title', 'Como cubrir este tema'))}</h2>
        <div class="story-copy compact">{about_html}</div>
      </div>
      <div class="sports-secondary">
        <div class="experience-grid">{experiences_html}</div>
      </div>
    </section>
    <section id="destinations" class="section">
      <div class="sports-primary">{destinations_html}</div>
    </section>
    <section class="section">
      <div class="itinerary-card wide">
        <p class="section-kicker">Agenda</p>
        <ol>{itinerary_html}</ol>
      </div>
    </section>
    {self._render_gallery_section(content, gallery_html)}
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
            """
        if variant in {"matchday", "locker", "sidebar", "showcase"}:
            return f"""
    <section id="about" class="section matchday-banner">
      <div class="scoreboard-side">
        <div class="metric-card sport-metric"><span>Matchday</span><strong>LIVE</strong></div>
        <div class="metric-card sport-metric"><span>Tema</span><strong>{escape(content.get('location', 'Tema'))}</strong></div>
      </div>
      <div class="scoreboard-main">
        <div class="story-copy compact">{about_html}</div>
      </div>
    </section>
    <section id="destinations" class="section">
      <div class="sports-grid">
        <div class="sports-primary">{destinations_html}</div>
        <div class="sports-secondary">
          <div class="itinerary-card"><ol>{itinerary_html}</ol></div>
          <div class="experience-grid">{experiences_html}</div>
        </div>
      </div>
    </section>
    {self._render_gallery_section(content, gallery_html)}
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
            """
        return f"""
    <section id="about" class="section sports-scoreboard">
      <div class="scoreboard-main">
        <p class="section-kicker">Editorial</p>
        <h2>{escape(content.get('about_title', 'Como cubrir este tema'))}</h2>
        <div class="story-copy compact">{about_html}</div>
      </div>
      <div class="scoreboard-side">
        <div class="metric-card sport-metric"><span>Ritmo</span><strong>ALTO</strong></div>
        <div class="metric-card sport-metric"><span>Foco</span><strong>{escape(content.get('location', 'Tema'))}</strong></div>
      </div>
    </section>

    <section id="destinations" class="section">
      <div class="section-head">
        <div>
          <p class="section-kicker">{escape(content.get('section_kicker', 'Secciones destacadas'))}</p>
          <h2>{escape(content.get('section_title', 'Como organizar un blog deportivo completo'))}</h2>
        </div>
        <p class="section-note">{escape(content.get('section_note', ''))}</p>
      </div>
      <div class="sports-grid">
        <div class="sports-primary">{destinations_html}</div>
        <div class="sports-secondary">
          <div class="itinerary-card">
            <p class="section-kicker">Calendario editorial</p>
            <h3>{escape(content.get('itinerary_title', 'Estructura editorial sugerida'))}</h3>
            <ol>{itinerary_html}</ol>
          </div>
          <div class="experience-grid">{experiences_html}</div>
        </div>
      </div>
    </section>

    {self._render_gallery_section(content, gallery_html)}
    {self._render_faq_section(faq_html)}
    {self._render_contact_section(content)}
        """

    def _render_gallery_section(self, content: dict, gallery_html: str) -> str:
        return f"""
    {self._render_article_section(content)}
    {self._render_support_media_section(content)}
    <section id="gallery" class="section">
      <div class="section-head">
        <div>
          <p class="section-kicker">Galeria</p>
          <h2>{escape(content.get('gallery_title', 'Imagenes de referencia con autoria visible'))}</h2>
        </div>
        <p class="section-note">{escape(content.get('credits_note', ''))}</p>
      </div>
      <div class="gallery-grid">{gallery_html}</div>
    </section>
    {self._render_references_section(content)}
        """

    def _render_support_media_section(self, content: dict) -> str:
        references = content.get("references", [])
        support_cards = []
        for reference in references[:3]:
            image_url = reference.get("image_url", "")
            if not image_url:
                continue
            support_cards.append(
                f"""
            <article class="support-card">
              <img src="{escape(image_url)}" alt="{escape(reference.get('title', 'Articulo de apoyo'))}">
              <div class="support-copy">
                <p class="card-tag">{escape(reference.get('source', 'Fuente'))}</p>
                <h3>{escape(reference.get('title', 'Articulo de apoyo'))}</h3>
                <p>{escape(self._sanitize_text(reference.get('snippet', '')))}</p>
                <a class="reference-link" href="{escape(reference.get('url', ''))}" target="_blank" rel="noreferrer">
                  {escape(reference.get('official_label', 'Ir a la pagina oficial'))}
                </a>
              </div>
            </article>
                """
            )
        if not support_cards:
            return ""
        return f"""
    <section class="section support-shell">
      <div class="section-head">
        <div>
          <p class="section-kicker">Apoyo visual</p>
          <h2>Imagenes y articulos relacionados</h2>
        </div>
        <p class="section-note">Cada tarjeta trae un resumen corto y acceso a la fuente principal cuando esta disponible.</p>
      </div>
      <div class="support-grid">{''.join(support_cards)}</div>
    </section>
        """

    def _render_article_section(self, content: dict) -> str:
        sections = content.get("article_sections", [])
        if not sections:
            return ""
        article_html = []
        for section in sections:
            paragraphs = "".join(f"<p>{escape(paragraph)}</p>" for paragraph in section.get("paragraphs", []))
            article_html.append(
                f"""
            <article class="article-card">
              <h3>{escape(section.get('title', 'Bloque editorial'))}</h3>
              {paragraphs}
            </article>
                """
            )
        return f"""
    <section class="section article-shell">
      <div class="section-head">
        <div>
          <p class="section-kicker">Contenido</p>
          <h2>{escape(content.get('article_title', 'Contenido desarrollado'))}</h2>
        </div>
        <p class="section-note">{escape(content.get('article_intro', ''))}</p>
      </div>
      <div class="article-grid">{''.join(article_html)}</div>
    </section>
        """

    def _render_references_section(self, content: dict) -> str:
        references = content.get("references", [])
        if not references:
            return ""
        cards = []
        for reference in references:
            title = escape(reference.get("title", "Referencia"))
            source = escape(reference.get("source", "Fuente"))
            snippet = escape(self._sanitize_text(reference.get("snippet", "")))
            label = escape(reference.get("label", "Lectura sugerida"))
            url = reference.get("url", "")
            cta = (
                f'<a class="reference-link" href="{escape(url)}" target="_blank" rel="noreferrer">{escape(reference.get("official_label", "Abrir referencia"))}</a>'
                if url
                else '<span class="reference-link muted-link">Sin enlace directo</span>'
            )
            cards.append(
                f"""
            <article class="reference-card">
              <p class="card-tag">{source}</p>
              <h3>{title}</h3>
              <p>{snippet}</p>
              <p class="reference-meta">{label}</p>
              {cta}
            </article>
                """
            )
        return f"""
    <section id="references" class="section reference-shell">
      <div class="section-head">
        <div>
          <p class="section-kicker">Referencias</p>
          <h2>{escape(content.get('references_title', 'Referencias y articulos recomendados'))}</h2>
        </div>
        <p class="section-note">{escape(content.get('references_intro', ''))}</p>
      </div>
      <div class="references-grid">{''.join(cards)}</div>
    </section>
        """

    def _render_faq_section(self, faq_html: str) -> str:
        return f"""
    <section id="faq" class="section faq-shell">
      <div class="section-head">
        <div>
          <p class="section-kicker">Preguntas frecuentes</p>
          <h2>Respuestas listas para el usuario final</h2>
        </div>
      </div>
      <div class="faq-list">{faq_html}</div>
    </section>
        """

    def _render_contact_section(self, content: dict) -> str:
        return f"""
    <section id="contact" class="section contact-shell">
      <div class="contact-copy">
        <p class="section-kicker">Contacto</p>
        <h2>{escape(content.get('contact_title', 'Conecta con tu audiencia'))}</h2>
        <p>{escape(content.get('contact_copy', ''))}</p>
      </div>
      <form class="contact-form">
        <input type="text" placeholder="Nombre">
        <input type="email" placeholder="Correo">
        <textarea placeholder="Cuentanos tu idea o consulta"></textarea>
        <button type="button">Enviar solicitud</button>
      </form>
    </section>
        """

    def _render_feature_cards(self, niche: str, content: dict) -> str:
        cards = []
        for item in content.get("featured_destinations", []):
            if niche == "turismo":
                fact_1 = item.get("schedule", "")
                fact_2 = item.get("price", "")
                fact_3 = item.get("duration", "")
                fact_4 = item.get("what_to_bring", "")
            else:
                fact_1 = item.get("meta_1", "")
                fact_2 = item.get("meta_2", "")
                fact_3 = item.get("meta_3", "")
                fact_4 = item.get("what_to_bring", "")
            cards.append(
                f"""
            <article class="destination-card">
              <p class="card-tag">{escape(item['tag'])}</p>
              <h3>{escape(item['title'])}</h3>
              <p>{escape(item['description'])}</p>
              <ul class="mini-facts">
                <li>{escape(fact_1)}</li>
                <li>{escape(fact_2)}</li>
                <li>{escape(fact_3)}</li>
              </ul>
              <p class="micro-note">{escape(fact_4)}</p>
            </article>
                """
            )
        return "".join(cards)

    def _render_gallery(self, images: list[dict]) -> str:
        if not images:
            return """
            <article class="gallery-card fallback-card">
              <div class="fallback-media">Sin imagenes externas disponibles</div>
              <div class="gallery-copy">
                <h3>Sube tus propias imagenes o vuelve a intentar mas tarde</h3>
                <p>El blog queda listo y mantiene el espacio preparado para una galeria con creditos claros.</p>
              </div>
            </article>
            """
        return "".join(
            f"""
            <article class="gallery-card">
              <img src="{escape(image['thumbnail_url'])}" alt="{escape(image['title'])}">
              <div class="gallery-copy">
                <p class="card-tag">{escape(image['source'])}</p>
                <h3>{escape(image['title'])}</h3>
                <p>Autor: {escape(image['author'])}</p>
                <p>Licencia: {escape(image['license'])}</p>
                <p class="attribution-line">{escape(self._plain_attribution(image))}</p>
              </div>
            </article>
            """
            for image in images
        )

    def _render_css(self, palette: dict) -> str:
        return f""":root {{
  --primary: {palette.get('primary', '#14532d')};
  --secondary: {palette.get('secondary', '#f59e0b')};
  --bg: {palette.get('background', '#f8fafc')};
  --text: {palette.get('text', '#0f172a')};
  --muted: #475569;
  --hero-accent: {palette.get('hero_accent', 'rgba(245,158,11,0.2)')};
  --surface-tint: {palette.get('surface_tint', 'rgba(20,83,45,0.06)')};
  --display-font: {palette.get('display_font', "Georgia, 'Times New Roman', serif")};
  --body-font: {palette.get('body_font', "Georgia, 'Times New Roman', serif")};
}}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{ margin: 0; font-family: var(--body-font); background: linear-gradient(180deg, #ffffff, var(--bg)); color: var(--text); }}
a {{ color: inherit; text-decoration: none; }}
img {{ display: block; max-width: 100%; }}
.hero {{
  padding: 24px;
  color: white;
  background:
    radial-gradient(circle at top left, var(--hero-accent), transparent 25%),
    linear-gradient(135deg, var(--primary), #052e16 56%, #0f172a 100%);
}}
.hero.hero-poster {{
  text-align: center;
  padding-bottom: 56px;
}}
.hero.hero-magazine {{
  background:
    radial-gradient(circle at 15% 15%, var(--hero-accent), transparent 20%),
    linear-gradient(135deg, #111827 0%, var(--primary) 45%, #0f172a 100%);
}}
.hero.hero-showcase {{
  background:
    radial-gradient(circle at 85% 20%, var(--hero-accent), transparent 22%),
    linear-gradient(135deg, var(--primary), #0f172a 52%, #111827 100%);
}}
.topbar {{ display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }}
.menu {{ display: flex; gap: 16px; flex-wrap: wrap; }}
.brand {{ font-size: 1.3rem; font-weight: 700; }}
.hero-grid {{
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(280px, 0.8fr);
  gap: 24px;
  align-items: end;
  max-width: 1180px;
  margin: 72px auto 24px;
}}
.hero-copy h1 {{ font-family: var(--display-font); font-size: clamp(2.7rem, 6vw, 5.2rem); line-height: 0.95; margin: 0.15em 0; }}
.hero-lead {{ font-size: 1.2rem; max-width: 720px; }}
.hero-support {{ max-width: 680px; color: rgba(255,255,255,0.86); }}
.hero-lead.centered, .hero-support.centered {{ margin-left: auto; margin-right: auto; }}
.hero-panel {{
  background: rgba(255,255,255,0.1);
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 28px;
  padding: 24px;
  backdrop-filter: blur(10px);
}}
.hero-panel ul {{ padding-left: 18px; margin-bottom: 0; }}
.hero-chip-row {{
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 24px;
}}
.hero-chip-row.compact {{ margin-top: 16px; }}
.hero-chip {{
  display: inline-flex;
  gap: 6px;
  align-items: center;
  border-radius: 999px;
  padding: 10px 14px;
  background: rgba(255,255,255,0.12);
  border: 1px solid rgba(255,255,255,0.18);
  font-size: 0.95rem;
}}
.hero-poster-shell {{
  max-width: 980px;
  margin: 72px auto 12px;
}}
.sidebar-hero {{
  grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.75fr);
}}
.magazine-hero {{
  grid-template-columns: minmax(0, 1.15fr) minmax(320px, 0.85fr);
  align-items: stretch;
}}
.showcase-hero {{
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr);
  align-items: center;
}}
.hero-copy.framed {{
  padding: 28px;
  border-radius: 30px;
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.15);
}}
.hero-panel-rail,
.magazine-panel,
.accent-panel {{
  background: rgba(255,255,255,0.12);
}}
.hero-showcase-stack {{
  display: grid;
  gap: 18px;
}}
.eyebrow, .section-kicker, .card-tag {{
  text-transform: uppercase;
  letter-spacing: 0.18em;
  font-size: 0.75rem;
  opacity: 0.9;
}}
.hero-actions {{ display: flex; gap: 16px; flex-wrap: wrap; margin-top: 24px; align-items: center; }}
.cta, button {{
  border: 0;
  border-radius: 999px;
  padding: 14px 22px;
  background: var(--secondary);
  color: #111827;
  font-weight: 700;
  cursor: pointer;
}}
.secondary-link {{ color: white; text-decoration: underline; text-underline-offset: 5px; }}
.section {{ padding: 76px 24px; max-width: 1180px; margin: 0 auto; }}
.section-head {{
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: end;
  flex-wrap: wrap;
  margin-bottom: 28px;
}}
.section-note {{ color: var(--muted); max-width: 460px; }}
.story-grid {{
  display: grid;
  grid-template-columns: minmax(220px, 0.8fr) minmax(0, 1.2fr);
  gap: 28px;
  align-items: start;
}}
.story-copy {{
  background: white;
  border-radius: 28px;
  padding: 28px;
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.08);
}}
.story-copy.compact {{ background: transparent; box-shadow: none; padding: 0; }}
.destination-grid, .experience-grid, .gallery-grid, .references-grid, .article-grid, .support-grid {{
  display: grid;
  gap: 20px;
}}
.destination-grid {{ grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }}
.experience-grid {{ grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }}
.references-grid {{ grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }}
.article-grid {{ grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }}
.support-grid {{ grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }}
.destination-card, .experience-card, .itinerary-card, .gallery-card, .reference-card, .article-card, .support-card, .faq-item, .contact-form {{
  background: white;
  border-radius: 28px;
  padding: 24px;
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.08);
}}
.destination-card {{
  background:
    linear-gradient(180deg, var(--surface-tint), rgba(255,255,255,1)),
    white;
}}
.mini-facts {{
  margin: 18px 0;
  padding-left: 18px;
  color: var(--muted);
}}
.micro-note {{ color: var(--muted); font-size: 0.95rem; margin: 0; }}
.split-layout {{
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(280px, 0.8fr);
  gap: 24px;
}}
.itinerary-card ol {{ padding-left: 20px; margin: 0; }}
.gallery-grid {{ grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }}
.gallery-card {{ overflow: hidden; padding: 0; }}
.support-card {{ overflow: hidden; padding: 0; }}
.gallery-card img {{ width: 100%; height: 220px; object-fit: cover; background: #e2e8f0; }}
.support-card img {{ width: 100%; height: 220px; object-fit: cover; background: #e2e8f0; }}
.gallery-copy {{ padding: 22px; }}
.support-copy {{ padding: 22px; display: grid; gap: 12px; }}
.attribution-line {{ color: var(--muted); font-size: 0.95rem; }}
.reference-card {{ display: grid; gap: 12px; align-content: start; }}
.article-card {{ display: grid; gap: 12px; align-content: start; }}
.reference-meta {{ color: var(--muted); font-size: 0.92rem; margin: 0; }}
.reference-link {{ display: inline-flex; align-items: center; width: fit-content; padding: 10px 16px; border-radius: 999px; background: var(--surface-tint); color: var(--primary); font-weight: 700; }}
.muted-link {{ opacity: 0.8; }}
.newsroom-shell, .sports-scoreboard {{
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(260px, 0.85fr);
  gap: 24px;
}}
.headline-card, .rail-card, .eco-statement, .eco-checklist, .impact-panel, .scoreboard-main, .scoreboard-side {{
  background: white;
  border-radius: 24px;
  padding: 24px;
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.08);
}}
.headline-card.accent {{
  background: linear-gradient(180deg, var(--surface-tint), rgba(255,255,255,1));
}}
.news-grid, .sports-grid {{
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(280px, 0.85fr);
  gap: 24px;
}}
.news-lead-stack, .eco-card-ribbon, .sports-primary, .news-side-rail, .sports-secondary {{
  display: grid;
  gap: 20px;
}}
.eco-intro {{
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 0.9fr);
  gap: 24px;
}}
.eco-card-ribbon .destination-card:nth-child(odd) {{
  transform: translateY(10px);
}}
.sport-metric {{
  background: linear-gradient(180deg, var(--surface-tint), rgba(255,255,255,1));
}}
.fallback-media {{
  min-height: 220px;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, rgba(20,83,45,0.2), rgba(245,158,11,0.3));
  color: #0f172a;
  font-weight: 700;
}}
.faq-list {{ display: grid; gap: 16px; }}
.faq-item summary {{ cursor: pointer; font-weight: 700; }}
.faq-item p {{ color: var(--muted); }}
.contact-shell {{
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr);
  gap: 24px;
  align-items: start;
}}
.contact-form {{ display: grid; gap: 12px; }}
input, textarea {{
  width: 100%;
  padding: 14px;
  border-radius: 16px;
  border: 1px solid #cbd5e1;
  font: inherit;
}}
textarea {{ min-height: 140px; resize: vertical; }}
.footer {{
  padding: 24px;
  text-align: center;
  background: #0f172a;
  color: white;
}}
.footer-note {{ color: rgba(255,255,255,0.72); font-size: 0.95rem; }}
.theme-noticias .hero {{
  background:
    linear-gradient(90deg, rgba(255,255,255,0.08) 1px, transparent 1px),
    linear-gradient(135deg, var(--primary), #111827 58%, #020617 100%);
  background-size: 22px 22px, auto;
}}
.theme-noticias .brand, .theme-noticias .hero-copy h1 {{ letter-spacing: -0.03em; }}
.theme-noticias .destination-card, .theme-noticias .experience-card {{
  border-top: 4px solid var(--secondary);
  border-radius: 14px;
}}
.theme-medio_ambiente .hero {{
  background:
    radial-gradient(circle at top left, rgba(132,204,22,0.22), transparent 25%),
    linear-gradient(135deg, var(--primary), #14532d 56%, #365314 100%);
}}
.theme-medio_ambiente .destination-card, .theme-medio_ambiente .itinerary-card {{
  border-radius: 32px 12px 32px 12px;
}}
.theme-deportes .hero {{
  background:
    linear-gradient(115deg, rgba(255,255,255,0.08) 0 18%, transparent 18% 32%, rgba(255,255,255,0.05) 32% 45%, transparent 45%),
    linear-gradient(135deg, var(--primary), #0f172a 58%, #111827 100%);
}}
.theme-deportes .hero-copy h1 {{ text-transform: uppercase; }}
.theme-deportes .destination-card, .theme-deportes .experience-card {{
  box-shadow: 0 18px 0 rgba(15, 23, 42, 0.04), 0 24px 60px rgba(15, 23, 42, 0.08);
}}
.theme-tecnologia .hero {{
  background:
    radial-gradient(circle at top left, rgba(56,189,248,0.22), transparent 22%),
    linear-gradient(135deg, var(--primary), #111827 56%, #1e293b 100%);
}}
.theme-tecnologia .destination-card, .theme-tecnologia .experience-card {{
  border: 1px solid rgba(59,130,246,0.14);
}}
.variant-postcard .hero-grid, .variant-magazine .hero-grid, .variant-arena .hero-grid, .variant-lab .hero-grid, .variant-sidebar .hero-grid {{
  grid-template-columns: minmax(260px, 0.85fr) minmax(0, 1.15fr);
}}
.variant-expedition .destination-card, .variant-canopy .destination-card {{
  border-radius: 34px 10px 34px 10px;
}}
.variant-newsroom .news-grid .destination-card, .variant-briefing .news-grid .destination-card {{
  border-left: 6px solid var(--secondary);
}}
.variant-magazine .headline-card, .variant-salon .headline-card, .variant-menu .destination-card {{
  border-radius: 10px;
}}
.variant-scoreboard .sports-grid .destination-card, .variant-matchday .sports-grid .destination-card {{
  transform: skewY(-1deg);
}}
.variant-matchday .sports-grid .destination-card > * {{
  transform: skewY(1deg);
}}
.variant-grid .destination-grid, .variant-lab .gallery-grid {{
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}}
.variant-manifesto .eco-intro .eco-statement, .variant-fieldnote .impact-panel {{
  border: 1px solid rgba(15,23,42,0.08);
}}
.variant-manifesto .hero-copy h1, .variant-poster .hero-copy h1, .variant-duel .hero-copy h1, .variant-campaign .hero-copy h1, .variant-landing .hero-copy h1 {{
  font-size: clamp(3rem, 7vw, 6rem);
}}
.variant-ledger .destination-card, .variant-studio .destination-card, .variant-summit .destination-card {{
  border-top: 5px solid var(--primary);
}}
@media (max-width: 900px) {{
  .hero-grid, .story-grid, .split-layout, .contact-shell, .newsroom-shell, .news-grid, .eco-intro, .sports-scoreboard, .sports-grid, .sidebar-hero, .magazine-hero, .showcase-hero {{ grid-template-columns: 1fr; }}
}}
@media (max-width: 768px) {{
  .menu {{ width: 100%; justify-content: center; }}
  .section {{ padding: 56px 18px; }}
}}
"""

    def _render_js(self, blog: Blog) -> str:
        return f"""document.addEventListener('DOMContentLoaded', () => {{
  const button = document.querySelector('.contact-form button');
  if (button) {{
    button.addEventListener('click', () => {{
      alert('Gracias por tu interes en {blog.title}. Puedes conectar este formulario a WhatsApp, correo o CRM.');
    }});
  }}
}});"""

    def _write_generated_files(self, slug: str, version: BlogVersion) -> None:
        target_dir = self.settings.generated_blogs_path / slug
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "index.html").write_text(version.html_content, encoding="utf-8")
        (target_dir / "style.css").write_text(version.css_content, encoding="utf-8")
        (target_dir / "app.js").write_text(version.js_content, encoding="utf-8")

    def _sanitize_text(self, value: str) -> str:
        return bleach.clean(value or "", tags=[], attributes={}, strip=True).strip()

    def _title_from_prompt(self, prompt: str) -> str:
        lowered = prompt.lower()
        niche = self._guess_niche(prompt)
        topic = self._extract_topic(prompt, niche)
        title_topic = self._editorial_topic(topic)
        location = self._extract_location(prompt)
        if self._is_sports_event_prompt(prompt, topic):
            return f"{title_topic}: selecciones, fechas y panorama"
        if "turismo" in lowered:
            return f"Descubre {location}: guia practica y visual"
        if "cafeter" in lowered or "cafe" in lowered:
            return f"{title_topic}: experiencia, sabor y propuesta"
        if niche == "noticias":
            return f"{title_topic}: claves, contexto y seguimiento"
        if niche == "medio_ambiente":
            return f"{title_topic}: contexto, impacto y accion"
        if niche == "deportes":
            return f"{title_topic}: agenda, protagonistas y analisis"
        if niche == "tecnologia":
            return f"{title_topic}: tendencias, uso y vision digital"
        return f"{title_topic}: guia editorial completa" if title_topic else "Nuevo blog"

    def _guess_niche(self, prompt: str) -> str:
        lowered = prompt.lower()
        if "noticia" in lowered or "news" in lowered or "actualidad" in lowered:
            return "noticias"
        if "turismo" in lowered:
            return "turismo"
        if "medio ambiente" in lowered or "ambiental" in lowered or "sostenib" in lowered or "ecolog" in lowered:
            return "medio_ambiente"
        if any(word in lowered for word in ("deporte", "futbol", "baloncesto", "tenis", "mundial", "fifa", "copa", "seleccion", "partido", "fixture", "torneo")):
            return "deportes"
        if "cafe" in lowered or "cafeter" in lowered:
            return "cafeteria"
        if "tecnolog" in lowered:
            return "tecnologia"
        return "corporativo"

    def _extract_location(self, prompt: str) -> str:
        lowered = prompt.lower()
        markers = [" en ", " de ", " para "]
        for marker in markers:
            if marker in lowered:
                fragment = prompt[lowered.rfind(marker) + len(marker):].strip(" .,!?:;")
                if fragment:
                    return fragment.title()
        return "tu destino"

    def _default_audience_for_niche(self, niche: str, location: str) -> str:
        if niche == "turismo":
            return f"Viajeros nacionales, visitantes regionales y operadores interesados en {location}"
        if niche == "noticias":
            return "Lectores que buscan actualidad, contexto y seguimiento claro"
        if niche == "medio_ambiente":
            return "Personas interesadas en sostenibilidad, educacion ambiental y cambio de habitos"
        if niche == "deportes":
            return "Aficionados, comunidades deportivas y lectores que siguen actualidad y analisis"
        if niche == "cafeteria":
            return "Clientes locales, turistas y amantes del cafe de especialidad"
        return "Clientes locales y visitantes digitales"

    def _default_sections_for_niche(self, niche: str) -> list[str]:
        if niche == "turismo":
            return ["hero", "destinations", "experiences", "gallery", "faq", "contact"]
        if niche == "noticias":
            return ["hero", "coverage", "analysis", "gallery", "faq", "contact"]
        if niche == "medio_ambiente":
            return ["hero", "problem", "solutions", "gallery", "faq", "contact"]
        if niche == "deportes":
            return ["hero", "agenda", "stories", "gallery", "faq", "contact"]
        return ["hero", "overview", "sections", "gallery", "faq", "contact"]

    def _default_seo_description(self, niche: str, prompt: str) -> str:
        topic = self._extract_topic(prompt, niche)
        if niche == "turismo":
            return f"Guia visual y practica para descubrir {topic}."
        if niche == "noticias":
            return f"Cobertura editorial clara y actual sobre {topic}."
        if niche == "medio_ambiente":
            return f"Contenido sobre {topic} con contexto, impacto y accion."
        if niche == "deportes":
            return f"Analisis, agenda e historias alrededor de {topic}."
        if niche == "tecnologia":
            return f"Blog enfocado en {topic} con enfoque digital y visual."
        if niche == "cafeteria":
            return f"Experiencia editorial y comercial sobre {topic}."
        return f"Blog visual y estructurado sobre {topic}."

    def _default_promise_for_niche(self, niche: str, prompt: str) -> str:
        topic = self._extract_topic(prompt, niche)
        if niche == "turismo":
            return f"Descubre planes, recomendaciones y lugares imperdibles en {topic}."
        if niche == "noticias":
            return f"Sigue {topic} con contexto, enfoque editorial y una lectura facil de escanear."
        if niche == "medio_ambiente":
            return f"Entiende {topic} con ejemplos, impacto visible y acciones concretas para la audiencia."
        if niche == "deportes":
            return f"Vive {topic} con agenda, protagonistas, analisis y energia visual."
        if niche == "tecnologia":
            return f"Explora {topic} con una puesta visual actual, clara y enfocada en innovacion."
        if niche == "cafeteria":
            return f"Presenta {topic} con atmosfera, producto y una experiencia lista para convertir."
        return f"Presenta {topic} con una estructura clara, identidad visual y contenido listo para personalizar."

    def _default_cta_for_niche(self, niche: str) -> str:
        if niche == "turismo":
            return "Planear mi visita"
        if niche == "noticias":
            return "Ver cobertura"
        if niche == "medio_ambiente":
            return "Explorar contenido"
        if niche == "deportes":
            return "Ver secciones"
        if niche == "tecnologia":
            return "Ver articulos"
        if niche == "cafeteria":
            return "Conocer la carta"
        return "Explorar blog"

    def _contact_copy_for_niche(self, niche: str) -> str:
        if niche == "turismo":
            return "Deja tus datos para recibir itinerarios, recomendaciones y propuestas personalizadas."
        if niche == "noticias":
            return "Usa este bloque para suscripciones, contacto editorial o envio de novedades."
        if niche == "medio_ambiente":
            return "Usa este espacio para voluntariado, alianzas, preguntas o propuestas ambientales."
        if niche == "deportes":
            return "Usa este bloque para newsletter, comunidad, membresias o contacto comercial."
        if niche == "tecnologia":
            return "Usa este espacio para demos, contacto, alianzas o captacion de leads."
        if niche == "cafeteria":
            return "Usa este bloque para reservas, pedidos, eventos o contacto con clientes."
        return "Usa este bloque para contacto, conversion, propuestas o captacion de leads."

    def _is_sports_event_prompt(self, prompt: str, topic: str) -> bool:
        text = f"{prompt} {topic}".lower()
        return any(word in text for word in ("mundial", "fifa", "copa del mundo", "fixture", "selecciones", "grupos", "sedes", "partidos", "clasificad"))

    def _build_sports_event_image_query(self, topic: str) -> str:
        return f"{topic} football world cup stadium fans national team match"

    def _build_image_query(self, niche: str, topic: str) -> str:
        if niche == "turismo":
            return f"{topic} travel landscape destination local culture"
        if niche == "noticias":
            return f"{topic} journalism reporting newsroom headline press"
        if niche == "medio_ambiente":
            return f"{topic} sustainability recycling environment community nature"
        if niche == "deportes":
            return self._build_sports_event_image_query(topic) if self._is_sports_event_prompt(topic, topic) else f"{topic} sport athlete competition training stadium"
        if niche == "tecnologia":
            return f"{topic} technology innovation digital product interface"
        if niche == "cafeteria":
            return f"{topic} coffee cafe specialty interior beverage"
        return f"{topic} editorial business brand website"

    def _default_palette_for_niche(self, niche: str) -> dict:
        palettes = {
            "turismo": {
                "primary": "#14532d",
                "secondary": "#f59e0b",
                "background": "#f8fafc",
                "text": "#0f172a",
                "hero_accent": "rgba(245,158,11,0.2)",
                "surface_tint": "rgba(20,83,45,0.06)",
                "display_font": "'DM Serif Display', Georgia, serif",
                "body_font": "'Source Serif 4', Georgia, serif",
            },
            "noticias": {
                "primary": "#991b1b",
                "secondary": "#facc15",
                "background": "#f8fafc",
                "text": "#111827",
                "hero_accent": "rgba(250,204,21,0.14)",
                "surface_tint": "rgba(153,27,27,0.05)",
                "display_font": "'Merriweather', Georgia, serif",
                "body_font": "'Libre Franklin', Arial, sans-serif",
            },
            "medio_ambiente": {
                "primary": "#166534",
                "secondary": "#84cc16",
                "background": "#f7fee7",
                "text": "#14532d",
                "hero_accent": "rgba(132,204,22,0.22)",
                "surface_tint": "rgba(22,101,52,0.06)",
                "display_font": "'Bitter', Georgia, serif",
                "body_font": "'Lora', Georgia, serif",
            },
            "deportes": {
                "primary": "#1d4ed8",
                "secondary": "#f97316",
                "background": "#eff6ff",
                "text": "#0f172a",
                "hero_accent": "rgba(249,115,22,0.18)",
                "surface_tint": "rgba(29,78,216,0.06)",
                "display_font": "'Oswald', Impact, sans-serif",
                "body_font": "'Barlow', Arial, sans-serif",
            },
            "tecnologia": {
                "primary": "#0f172a",
                "secondary": "#38bdf8",
                "background": "#f8fafc",
                "text": "#0f172a",
                "hero_accent": "rgba(56,189,248,0.18)",
                "surface_tint": "rgba(15,23,42,0.05)",
                "display_font": "'Space Grotesk', Arial, sans-serif",
                "body_font": "'IBM Plex Sans', Arial, sans-serif",
            },
            "cafeteria": {
                "primary": "#78350f",
                "secondary": "#f59e0b",
                "background": "#fffbeb",
                "text": "#3f2a1d",
                "hero_accent": "rgba(245,158,11,0.18)",
                "surface_tint": "rgba(120,53,15,0.06)",
                "display_font": "'Fraunces', Georgia, serif",
                "body_font": "'Lora', Georgia, serif",
            },
        }
        return palettes.get(
            niche,
            {
                "primary": "#0f172a",
                "secondary": "#f59e0b",
                "background": "#f8fafc",
                "text": "#0f172a",
                "hero_accent": "rgba(245,158,11,0.18)",
                "surface_tint": "rgba(15,23,42,0.05)",
                "display_font": "'Merriweather', Georgia, serif",
                "body_font": "'Source Serif 4', Georgia, serif",
            },
        )

    def _palette_for_request(self, niche: str, prompt: str, provided: dict | None, sequence_index: int = 0) -> dict:
        if provided:
            return provided
        variants = self._palette_variants_for_niche(niche)
        if not variants:
            return self._default_palette_for_niche(niche)
        base = int(hashlib.sha256(f"{niche}|{prompt}".encode("utf-8")).hexdigest(), 16)
        index = (base + sequence_index) % len(variants)
        return variants[index]

    def _palette_variants_for_niche(self, niche: str) -> list[dict]:
        variants = {
            "turismo": [
                {"primary": "#14532d", "secondary": "#f59e0b", "background": "#f8fafc", "text": "#0f172a", "hero_accent": "rgba(245,158,11,0.2)", "surface_tint": "rgba(20,83,45,0.06)", "display_font": "'DM Serif Display', Georgia, serif", "body_font": "'Source Serif 4', Georgia, serif"},
                {"primary": "#0f766e", "secondary": "#f97316", "background": "#f0fdfa", "text": "#134e4a", "hero_accent": "rgba(249,115,22,0.18)", "surface_tint": "rgba(15,118,110,0.06)", "display_font": "'DM Serif Display', Georgia, serif", "body_font": "'Source Serif 4', Georgia, serif"},
                {"primary": "#1d4ed8", "secondary": "#f59e0b", "background": "#eff6ff", "text": "#172554", "hero_accent": "rgba(245,158,11,0.16)", "surface_tint": "rgba(29,78,216,0.06)", "display_font": "'DM Serif Display', Georgia, serif", "body_font": "'Source Serif 4', Georgia, serif"},
                {"primary": "#7c3aed", "secondary": "#22c55e", "background": "#faf5ff", "text": "#2e1065", "hero_accent": "rgba(34,197,94,0.14)", "surface_tint": "rgba(124,58,237,0.06)", "display_font": "'DM Serif Display', Georgia, serif", "body_font": "'Source Serif 4', Georgia, serif"},
            ],
            "noticias": [
                {"primary": "#991b1b", "secondary": "#facc15", "background": "#f8fafc", "text": "#111827", "hero_accent": "rgba(250,204,21,0.14)", "surface_tint": "rgba(153,27,27,0.05)", "display_font": "'Merriweather', Georgia, serif", "body_font": "'Libre Franklin', Arial, sans-serif"},
                {"primary": "#1e3a8a", "secondary": "#fb7185", "background": "#f8fafc", "text": "#0f172a", "hero_accent": "rgba(251,113,133,0.14)", "surface_tint": "rgba(30,58,138,0.05)", "display_font": "'Merriweather', Georgia, serif", "body_font": "'Libre Franklin', Arial, sans-serif"},
                {"primary": "#0f172a", "secondary": "#22c55e", "background": "#f8fafc", "text": "#111827", "hero_accent": "rgba(34,197,94,0.16)", "surface_tint": "rgba(15,23,42,0.05)", "display_font": "'Merriweather', Georgia, serif", "body_font": "'Libre Franklin', Arial, sans-serif"},
                {"primary": "#7c2d12", "secondary": "#f59e0b", "background": "#fff7ed", "text": "#431407", "hero_accent": "rgba(245,158,11,0.16)", "surface_tint": "rgba(124,45,18,0.05)", "display_font": "'Merriweather', Georgia, serif", "body_font": "'Libre Franklin', Arial, sans-serif"},
            ],
            "medio_ambiente": [
                {"primary": "#166534", "secondary": "#84cc16", "background": "#f7fee7", "text": "#14532d", "hero_accent": "rgba(132,204,22,0.22)", "surface_tint": "rgba(22,101,52,0.06)", "display_font": "'Bitter', Georgia, serif", "body_font": "'Lora', Georgia, serif"},
                {"primary": "#0f766e", "secondary": "#facc15", "background": "#ecfeff", "text": "#134e4a", "hero_accent": "rgba(20,184,166,0.18)", "surface_tint": "rgba(15,118,110,0.06)", "display_font": "'Bitter', Georgia, serif", "body_font": "'Lora', Georgia, serif"},
                {"primary": "#3f6212", "secondary": "#f97316", "background": "#fefce8", "text": "#365314", "hero_accent": "rgba(249,115,22,0.16)", "surface_tint": "rgba(63,98,18,0.06)", "display_font": "'Bitter', Georgia, serif", "body_font": "'Lora', Georgia, serif"},
                {"primary": "#155e75", "secondary": "#a3e635", "background": "#ecfeff", "text": "#164e63", "hero_accent": "rgba(163,230,53,0.16)", "surface_tint": "rgba(21,94,117,0.06)", "display_font": "'Bitter', Georgia, serif", "body_font": "'Lora', Georgia, serif"},
            ],
            "deportes": [
                {"primary": "#1d4ed8", "secondary": "#f97316", "background": "#eff6ff", "text": "#0f172a", "hero_accent": "rgba(249,115,22,0.18)", "surface_tint": "rgba(29,78,216,0.06)", "display_font": "'Oswald', Impact, sans-serif", "body_font": "'Barlow', Arial, sans-serif"},
                {"primary": "#7c2d12", "secondary": "#22c55e", "background": "#fff7ed", "text": "#431407", "hero_accent": "rgba(34,197,94,0.18)", "surface_tint": "rgba(124,45,18,0.06)", "display_font": "'Oswald', Impact, sans-serif", "body_font": "'Barlow', Arial, sans-serif"},
                {"primary": "#7e22ce", "secondary": "#facc15", "background": "#faf5ff", "text": "#3b0764", "hero_accent": "rgba(250,204,21,0.18)", "surface_tint": "rgba(126,34,206,0.06)", "display_font": "'Oswald', Impact, sans-serif", "body_font": "'Barlow', Arial, sans-serif"},
                {"primary": "#0f172a", "secondary": "#ef4444", "background": "#f8fafc", "text": "#111827", "hero_accent": "rgba(239,68,68,0.18)", "surface_tint": "rgba(15,23,42,0.06)", "display_font": "'Oswald', Impact, sans-serif", "body_font": "'Barlow', Arial, sans-serif"},
            ],
            "tecnologia": [
                {"primary": "#0f172a", "secondary": "#38bdf8", "background": "#f8fafc", "text": "#0f172a", "hero_accent": "rgba(56,189,248,0.18)", "surface_tint": "rgba(15,23,42,0.05)", "display_font": "'Space Grotesk', Arial, sans-serif", "body_font": "'IBM Plex Sans', Arial, sans-serif"},
                {"primary": "#111827", "secondary": "#a855f7", "background": "#faf5ff", "text": "#111827", "hero_accent": "rgba(168,85,247,0.18)", "surface_tint": "rgba(17,24,39,0.05)", "display_font": "'Space Grotesk', Arial, sans-serif", "body_font": "'IBM Plex Sans', Arial, sans-serif"},
                {"primary": "#0b3b66", "secondary": "#22d3ee", "background": "#ecfeff", "text": "#082f49", "hero_accent": "rgba(34,211,238,0.16)", "surface_tint": "rgba(11,59,102,0.05)", "display_font": "'Space Grotesk', Arial, sans-serif", "body_font": "'IBM Plex Sans', Arial, sans-serif"},
                {"primary": "#18181b", "secondary": "#f97316", "background": "#fafaf9", "text": "#18181b", "hero_accent": "rgba(249,115,22,0.16)", "surface_tint": "rgba(24,24,27,0.05)", "display_font": "'Space Grotesk', Arial, sans-serif", "body_font": "'IBM Plex Sans', Arial, sans-serif"},
            ],
            "cafeteria": [
                {"primary": "#78350f", "secondary": "#f59e0b", "background": "#fffbeb", "text": "#3f2a1d", "hero_accent": "rgba(245,158,11,0.18)", "surface_tint": "rgba(120,53,15,0.06)", "display_font": "'Fraunces', Georgia, serif", "body_font": "'Lora', Georgia, serif"},
                {"primary": "#4c1d95", "secondary": "#fb7185", "background": "#fdf4ff", "text": "#3b0764", "hero_accent": "rgba(251,113,133,0.18)", "surface_tint": "rgba(76,29,149,0.06)", "display_font": "'Fraunces', Georgia, serif", "body_font": "'Lora', Georgia, serif"},
                {"primary": "#14532d", "secondary": "#f59e0b", "background": "#f0fdf4", "text": "#14532d", "hero_accent": "rgba(245,158,11,0.16)", "surface_tint": "rgba(20,83,45,0.06)", "display_font": "'Fraunces', Georgia, serif", "body_font": "'Lora', Georgia, serif"},
                {"primary": "#7c2d12", "secondary": "#fbbf24", "background": "#fff7ed", "text": "#431407", "hero_accent": "rgba(251,191,36,0.16)", "surface_tint": "rgba(124,45,18,0.06)", "display_font": "'Fraunces', Georgia, serif", "body_font": "'Lora', Georgia, serif"},
            ],
            "corporativo": [
                {"primary": "#0f172a", "secondary": "#f59e0b", "background": "#f8fafc", "text": "#0f172a", "hero_accent": "rgba(245,158,11,0.18)", "surface_tint": "rgba(15,23,42,0.05)", "display_font": "'Merriweather', Georgia, serif", "body_font": "'Source Serif 4', Georgia, serif"},
                {"primary": "#1d4ed8", "secondary": "#22c55e", "background": "#f0f9ff", "text": "#0f172a", "hero_accent": "rgba(34,197,94,0.16)", "surface_tint": "rgba(29,78,216,0.05)", "display_font": "'Merriweather', Georgia, serif", "body_font": "'Source Serif 4', Georgia, serif"},
                {"primary": "#334155", "secondary": "#fb7185", "background": "#f8fafc", "text": "#1e293b", "hero_accent": "rgba(251,113,133,0.16)", "surface_tint": "rgba(51,65,85,0.05)", "display_font": "'Merriweather', Georgia, serif", "body_font": "'Source Serif 4', Georgia, serif"},
                {"primary": "#0f766e", "secondary": "#f59e0b", "background": "#f0fdfa", "text": "#134e4a", "hero_accent": "rgba(245,158,11,0.16)", "surface_tint": "rgba(15,118,110,0.05)", "display_font": "'Merriweather', Georgia, serif", "body_font": "'Source Serif 4', Georgia, serif"},
            ],
        }
        return variants.get(niche, [])

    def _style_variant_for_request(self, niche: str, prompt: str, sequence_index: int = 0, recent: list[str] | None = None) -> str:
        variants = {
            "turismo": ["atlas", "postcard", "expedition", "souvenir", "trail", "landing", "showcase", "catalog"],
            "noticias": ["newsroom", "briefing", "magazine", "bulletin", "feature", "wire", "sidebar", "poster"],
            "medio_ambiente": ["canopy", "fieldnote", "manifesto", "journal", "campaign", "grid", "poster", "sidebar"],
            "deportes": ["scoreboard", "arena", "matchday", "duel", "locker", "poster", "showcase", "sidebar"],
            "tecnologia": ["signal", "lab", "grid", "terminal", "pulse", "showcase", "sidebar", "landing"],
            "cafeteria": ["roastery", "menu", "salon", "counter", "brew", "catalog", "showcase", "postcard"],
            "corporativo": ["ledger", "studio", "summit", "boardroom", "folio", "landing", "sidebar", "showcase"],
        }
        options = variants.get(niche, ["studio", "atlas"])
        return self._pick_rotating_variant(options, niche, prompt, sequence_index, recent or [])

    def _next_visual_index(self, db: Session, niche: str) -> int:
        return int(db.scalar(select(func.count(Blog.id)).where(Blog.niche == niche)) or 0)

    def _recent_visual_history(self, db: Session, niche: str, limit: int = 4) -> list[str]:
        rows = list(
            db.scalars(
                select(Blog)
                .where(Blog.niche == niche)
                .order_by(Blog.created_at.desc())
                .limit(limit)
            ).all()
        )
        history = []
        for row in rows:
            variant = (row.metadata_json or {}).get("content", {}).get("style_variant")
            if variant:
                history.append(variant)
        return history

    def _pick_rotating_variant(
        self,
        options: list[str],
        niche: str,
        prompt: str,
        sequence_index: int,
        recent: list[str],
    ) -> str:
        if not options:
            return "studio"
        base = int(hashlib.sha256(f"variant|{niche}|{prompt}".encode("utf-8")).hexdigest(), 16)
        preferred = (base + sequence_index) % len(options)
        recent_set = set(recent[:2])
        for offset in range(len(options)):
            candidate = options[(preferred + offset) % len(options)]
            if candidate not in recent_set:
                return candidate
        return options[preferred]

    def _extract_topic(self, prompt: str, niche: str) -> str:
        lowered = prompt.lower().strip()
        if niche == "turismo":
            return self._extract_location(prompt)
        topic = self._strip_blog_request_prefix(prompt)
        topic = self._strip_leading_niche_marker(topic, niche)
        topic = self._strip_request_suffixes(topic)
        cleaned = self._sanitize_text(topic).strip(" .,!?:;")
        return cleaned.title() if cleaned else niche.replace("_", " ").title()

    def _strip_blog_request_prefix(self, prompt: str) -> str:
        lowered = prompt.lower().strip()
        prefixes = [
            "hazme un blog sobre ",
            "haz un blog sobre ",
            "hazme un blog de ",
            "haz un blog de ",
            "crea un blog sobre ",
            "crear un blog sobre ",
            "crea un blog de ",
            "crear un blog de ",
            "necesito un blog sobre ",
            "necesito un blog de ",
            "quiero un blog sobre ",
            "quiero un blog de ",
            "blog sobre ",
            "blog de ",
        ]
        for prefix in prefixes:
            if lowered.startswith(prefix):
                return prompt[len(prefix):]
        return prompt

    def _strip_request_suffixes(self, topic: str) -> str:
        lowered = topic.lower()
        cut_markers = [
            " con imagenes",
            " con imágenes",
            " con referencias",
            " con articulos",
            " con artículos",
            " y que ",
            " que incluya ",
            " que tenga ",
            " agrega ",
            " agrega ",
            " y agrega ",
        ]
        cut_positions = [lowered.find(marker) for marker in cut_markers if lowered.find(marker) > 0]
        if cut_positions:
            topic = topic[: min(cut_positions)]
        return topic.strip()

    def _strip_leading_niche_marker(self, topic: str, niche: str) -> str:
        lowered = topic.lower().strip()
        markers = {
            "tecnologia": ["tecnologia sobre ", "tecnología sobre ", "tecnologia de ", "tecnología de "],
            "noticias": ["noticias sobre ", "noticia sobre "],
            "medio_ambiente": ["medio ambiente sobre ", "medio ambiente de "],
            "deportes": ["deportes sobre ", "deporte sobre "],
            "cafeteria": ["cafeteria sobre ", "cafe sobre ", "café sobre "],
        }.get(niche, [])
        for marker in markers:
            if lowered.startswith(marker):
                return topic[len(marker):]
        return topic

    def _editorial_topic(self, topic: str) -> str:
        clean = self._sanitize_text(topic).strip(" .,!?:;")
        if not clean:
            return "Nuevo blog"
        words = [word for word in clean.split() if word]
        if len(words) <= 10:
            return " ".join(word.capitalize() if word.islower() else word for word in words)
        trimmed = " ".join(words[:10]).strip()
        return " ".join(word.capitalize() if word.islower() else word for word in trimmed.split())

    def _clean_generated_title(self, title: str, prompt: str) -> str:
        lowered = title.lower().strip()
        banned_starts = (
            "hazme un blog",
            "haz un blog",
            "crea un blog",
            "crear un blog",
            "blog de",
            "blog sobre",
        )
        if any(lowered.startswith(start) for start in banned_starts):
            return self._title_from_prompt(prompt)
        if len(title.split()) < 3:
            return self._title_from_prompt(prompt)
        return title

    def _guide_mode_for_prompt(self, prompt: str, niche: str) -> str:
        lowered = prompt.lower()
        if niche == "deportes" and any(word in lowered for word in ("mundial", "fifa", "torneo", "copa", "selecciones", "fechas", "sedes")):
            return "guide"
        if any(word in lowered for word in ("guia", "guía", "explica", "resumen completo", "contexto")):
            return "guide"
        return "standard"

    def _default_destinations(self, niche: str, location: str) -> list[dict]:
        return [
            {
                "tag": "Destacado",
                "title": f"Lo mejor de {location}",
                "description": "Contenido pensado para mostrar por que este destino merece una visita.",
                "schedule": "Horario referencial: consulta disponibilidad local.",
                "price": "Precio referencial: valida costos actuales con el operador.",
                "duration": "Tiempo recomendado: 2 a 3 horas.",
                "tips": "Confirma acceso, clima y transporte antes de salir.",
                "what_to_bring": "Agua, ropa comoda, celular y dinero para gastos menores.",
                "note": "Los valores pueden variar segun temporada y proveedor.",
            },
            {
                "tag": "Experiencia",
                "title": "Momentos recomendados",
                "description": "Espacios para actividades, historias y recomendaciones utiles para el visitante.",
                "schedule": "Franja sugerida: manana o tarde segun clima.",
                "price": "Costo referencial: depende del plan elegido.",
                "duration": "Tiempo recomendado: 1 a 2 horas.",
                "tips": "Aprovecha horas de mejor luz si quieres fotos o video.",
                "what_to_bring": "Bloqueador, gorra y bateria externa.",
                "note": "Valida si hay cierres temporales o restricciones especiales.",
            },
            {
                "tag": "Planifica",
                "title": "Consejos practicos",
                "description": "Orientacion breve para convertir curiosidad en una visita real.",
                "schedule": "Consulta horarios oficiales antes de salir.",
                "price": "Reserva un presupuesto flexible para entradas y consumo.",
                "duration": "Tiempo recomendado: ajustable al itinerario.",
                "tips": "Guarda ubicaciones y telefonos utiles antes del viaje.",
                "what_to_bring": "Documento, agua, calzado comodo y efectivo.",
                "note": "Usa esta guia como orientacion inicial y confirma los datos clave localmente.",
            },
        ]

    def _build_faqs(self, data: dict, location: str) -> list[dict]:
        if data.get("faqs"):
            return [
                {
                    "question": self._sanitize_text(item.get("question", "Pregunta frecuente")),
                    "answer": self._sanitize_text(item.get("answer", "Respuesta pendiente")),
                }
                for item in data["faqs"]
            ]
        niche = data.get("niche", "")
        if niche == "noticias":
            return [
                {"question": f"Como cubrir {location} sin que el blog se vea genérico?", "answer": "Separando titulares, contexto, seguimiento y analisis para que cada bloque tenga una funcion distinta."},
                {"question": "Que debe tener un buen blog de noticias?", "answer": "Jerarquia editorial, rapidez de lectura, contexto suficiente y una identidad visual clara."},
                {"question": "Las imagenes se pueden reutilizar libremente?", "answer": "No siempre. Revisa autor, fuente y licencia antes de publicarlas."},
            ]
        if niche == "medio_ambiente":
            return [
                {"question": f"Como hacer atractivo un blog sobre {location}?", "answer": "Aterrizando el tema en ejemplos, soluciones, comunidad y acciones concretas para el lector."},
                {"question": "Que debe evitar un blog ambiental?", "answer": "Quedarse en mensajes abstractos sin explicar impacto, contexto o posibilidades de accion."},
                {"question": "Las imagenes se pueden reutilizar libremente?", "answer": "No siempre. Revisa autor, fuente y licencia antes de publicarlas."},
            ]
        if niche == "deportes":
            return [
                {"question": f"Como mantener vivo un blog de {location}?", "answer": "Combinando agenda, protagonistas, analisis y momentos destacados para que el lector siempre encuentre algo nuevo."},
                {"question": "Que hace fuerte a un blog deportivo?", "answer": "Ritmo editorial, comunidad, actualidad y una mirada propia sobre el tema."},
                {"question": "Las imagenes se pueden reutilizar libremente?", "answer": "No siempre. Revisa autor, fuente y licencia antes de publicarlas."},
            ]
        return [
            {"question": f"Como enfocar bien un blog sobre {location}?", "answer": "Definiendo una promesa clara, secciones con funciones distintas y una experiencia visual coherente con el nicho."},
            {"question": "Que tipo de contenido puedo publicar con este blog?", "answer": "Articulos, recursos, historias, bloques visuales y llamadas a la accion alineadas al tema principal."},
            {"question": "Las imagenes se pueden reutilizar libremente?", "answer": "No siempre. Revisa autor, fuente y licencia antes de publicarlas."},
        ]
