import asyncio
import io
import logging
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import get_settings
from app.database import SessionLocal
from app.models import Blog
from app.services.blog_generator import BlogGeneratorService
from app.services.speech_service import SpeechService


logging.basicConfig(level=logging.INFO)
settings = get_settings()
generator = BlogGeneratorService()
speech = SpeechService()


def _allowed(chat_id: int) -> bool:
    allowed = settings.allowed_chat_ids
    return not allowed or chat_id in allowed


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not _allowed(update.effective_chat.id):
        return
    await update.message.reply_text(
        "BlogBot IA activo. Usa /crear_blog seguido del prompt, escribe directamente 'Crea un blog sobre...' o enviame una nota de voz con tu pedido."
    )


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/crear_blog tema\n/mis_blogs\n/ver_blog <id>\n/editar_blog <id> | instruccion\n/publicar <id>\n/exportar_zip <id>\n/eliminar_blog <id>\nTambien puedes enviarme audio con tu pedido."
    )


async def crear_blog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    if not _allowed(update.effective_chat.id):
        return
    prompt = " ".join(context.args).strip() or update.message.text.replace("/crear_blog", "", 1).strip()
    if not prompt:
        await update.message.reply_text("Enviame un prompt, por ejemplo: /crear_blog Blog de turismo en Putumayo")
        return
    await update.message.reply_text("Generando blog y preparando vista previa...")
    with SessionLocal() as db:
        blog = await generator.create_blog(
            db,
            f"telegram_{update.effective_chat.id}",
            prompt,
            telegram_chat_id=str(update.effective_chat.id),
        )
    await _reply_blog_created(update, blog)


async def mis_blogs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    with SessionLocal() as db:
        blogs = (
            db.query(Blog)
            .filter(Blog.owner.has(username=f"telegram_{update.effective_chat.id}"))
            .order_by(Blog.updated_at.desc())
            .limit(10)
            .all()
        )
    if not blogs:
        await update.message.reply_text("Aun no tienes blogs registrados.")
        return
    lines = [f"{blog.id} | {blog.title} | {blog.status}" for blog in blogs]
    await update.message.reply_text("\n".join(lines))


async def ver_blog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not context.args:
        return
    blog_id = int(context.args[0])
    with SessionLocal() as db:
        blog = db.get(Blog, blog_id)
    if not blog:
        await update.message.reply_text("No encontre ese blog.")
        return
    await update.message.reply_text(f"{blog.title}\n{blog.preview_url}\nEstado: {blog.status}")


async def editar_blog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    payload = update.message.text.replace("/editar_blog", "", 1).strip()
    if "|" not in payload:
        await update.message.reply_text("Usa: /editar_blog 1 | Cambia el color principal a verde")
        return
    raw_id, instruction = [item.strip() for item in payload.split("|", 1)]
    with SessionLocal() as db:
        blog = db.get(Blog, int(raw_id))
        if not blog:
            await update.message.reply_text("No encontre ese blog.")
            return
        version = await generator.edit_blog(db, blog, instruction)
    await update.message.reply_text(f"Blog actualizado a la version {version.version_number}.")


async def publicar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not context.args:
        return
    with SessionLocal() as db:
        blog = db.get(Blog, int(context.args[0]))
        if not blog:
            await update.message.reply_text("No encontre ese blog.")
            return
        generator.publish_blog(db, blog)
    await update.message.reply_text(f"Publicado: {blog.published_url}")


async def exportar_zip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not context.args:
        return
    blog_id = context.args[0]
    await update.message.reply_text(f"Descarga el ZIP desde {settings.public_base_url}/api/blogs/{blog_id}/export")


async def eliminar_blog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not context.args:
        return
    with SessionLocal() as db:
        blog = db.get(Blog, int(context.args[0]))
        if not blog:
            await update.message.reply_text("No encontre ese blog.")
            return
        db.delete(blog)
        db.commit()
    await update.message.reply_text("Blog eliminado.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    lowered = text.lower()
    if lowered.startswith(("crea un blog", "haz un blog", "hazme un blog", "crear un blog")):
        context.args = text.split()[3:]
        await crear_blog(update, context)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    if not _allowed(update.effective_chat.id):
        return
    voice = update.message.voice or update.message.audio
    if not voice:
        return

    stt_status = speech.stt_status()
    if not stt_status.get("available"):
        await update.message.reply_text(f"No puedo transcribir audio ahora mismo. Motivo: {stt_status.get('reason', 'STT no disponible')}")
        return

    await update.message.reply_text("Recibi tu audio. Lo estoy transcribiendo y preparando el blog...")
    file = await context.bot.get_file(voice.file_id)
    suffix = ".ogg" if update.message.voice else ".mp3"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
        temp_path = Path(temp.name)
    try:
        await file.download_to_drive(custom_path=str(temp_path))
        result = await speech.transcribe_file(temp_path)
        if not result.get("available") or not result.get("text"):
            await update.message.reply_text(result.get("reason") or "No pude transcribir el audio.")
            return
        prompt = result["text"].strip()
        await update.message.reply_text(f"Entendi esto:\n{prompt}\n\nAhora estoy creando tu blog...")
        with SessionLocal() as db:
            blog = await generator.create_blog(
                db,
                f"telegram_{update.effective_chat.id}",
                prompt,
                telegram_chat_id=str(update.effective_chat.id),
            )
        await _reply_blog_created(update, blog)
    finally:
        temp_path.unlink(missing_ok=True)


async def _reply_blog_created(update: Update, blog: Blog) -> None:
    if not update.message:
        return
    message = f"Aqui esta tu blog: {blog.title}. Vista previa: {blog.preview_url}"
    await update.message.reply_text(f"Listo: {blog.title}\nVista previa: {blog.preview_url}\nID: {blog.id}")
    audio_bytes, status = await speech.synthesize_bytes(message)
    if audio_bytes:
        buffer = io.BytesIO(audio_bytes)
        buffer.name = "blogbot-respuesta.wav"
        buffer.seek(0)
        await update.message.reply_audio(audio=buffer, title="BlogBot IA", filename="blogbot-respuesta.wav")
    elif status.get("reason"):
        await update.message.reply_text(f"No pude enviarte la respuesta en audio: {status['reason']}")


def build_application() -> Application:
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ayuda", ayuda))
    app.add_handler(CommandHandler("crear_blog", crear_blog))
    app.add_handler(CommandHandler("mis_blogs", mis_blogs))
    app.add_handler(CommandHandler("ver_blog", ver_blog))
    app.add_handler(CommandHandler("editar_blog", editar_blog))
    app.add_handler(CommandHandler("publicar", publicar))
    app.add_handler(CommandHandler("exportar_zip", exportar_zip))
    app.add_handler(CommandHandler("eliminar_blog", eliminar_blog))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    return app


async def main() -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("Define TELEGRAM_BOT_TOKEN en .env para iniciar el bot.")
    app = build_application()
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
