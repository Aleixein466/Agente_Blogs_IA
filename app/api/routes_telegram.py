from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.dependencies import db_session
from app.schemas import TelegramGenerateRequest
from app.services.blog_generator import BlogGeneratorService


router = APIRouter(prefix="/api/telegram", tags=["telegram"])
generator = BlogGeneratorService()


@router.post("/generate")
async def generate_from_telegram(payload: TelegramGenerateRequest, db: Session = Depends(db_session)):
    username = payload.username or f"telegram_{payload.chat_id}"
    blog = await generator.create_blog(db, username, payload.text, telegram_chat_id=str(payload.chat_id))
    return {
        "message": f"Blog '{blog.title}' creado",
        "preview_url": blog.preview_url,
        "blog_id": blog.id,
    }
