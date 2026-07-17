from fastapi import APIRouter

from app.api.routes_admin import router as admin_router
from app.api.routes_blogs import router as blogs_router
from app.api.routes_telegram import router as telegram_router
from app.api.routes_voice import router as voice_router
from app.api.routes_web import router as web_router


api_router = APIRouter()
api_router.include_router(web_router)
api_router.include_router(blogs_router)
api_router.include_router(admin_router)
api_router.include_router(telegram_router)
api_router.include_router(voice_router)
