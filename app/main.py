from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.router import api_router
from app.config import get_settings
from app.database import Base, engine


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    for path in (settings.generated_blogs_path, settings.uploads_path, settings.logs_path, settings.model_cache_path):
        Path(path).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax", https_only=False)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "static")), name="static")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "model": settings.ollama_model}
