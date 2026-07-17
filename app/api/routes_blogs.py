import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.dependencies import db_session
from app.models import Blog, BlogImage, BlogMessage, BlogVersion, PromptHistory
from app.schemas import BlogCreateRequest, BlogEditRequest, BlogPublishRequest, BlogResponse, BlogVersionResponse
from app.services.blog_generator import BlogGeneratorService
from app.services.export_service import ExportService
from app.services.image_service import ImageService


router = APIRouter(prefix="/api/blogs", tags=["blogs"])
generator = BlogGeneratorService()
image_service = ImageService()
export_service = ExportService()


@router.get("", response_model=list[BlogResponse])
def list_blogs(db: Session = Depends(db_session)) -> list[Blog]:
    return list(db.scalars(select(Blog).order_by(Blog.created_at.desc())).all())


@router.post("", response_model=BlogResponse)
async def create_blog(payload: BlogCreateRequest, db: Session = Depends(db_session)) -> Blog:
    return await generator.create_blog(db, payload.owner_username, payload.prompt)


@router.get("/{blog_id}", response_model=BlogResponse)
def get_blog(blog_id: int, db: Session = Depends(db_session)) -> Blog:
    blog = db.get(Blog, blog_id)
    if not blog:
        raise HTTPException(status_code=404, detail="Blog not found")
    return blog


@router.get("/{blog_id}/version", response_model=BlogVersionResponse)
def get_current_version(blog_id: int, db: Session = Depends(db_session)):
    blog = db.get(Blog, blog_id)
    if not blog or not blog.current_version:
        raise HTTPException(status_code=404, detail="Version not found")
    return blog.current_version


@router.post("/{blog_id}/edit", response_model=BlogVersionResponse)
async def edit_blog(blog_id: int, payload: BlogEditRequest, db: Session = Depends(db_session)):
    blog = db.get(Blog, blog_id)
    if not blog:
        raise HTTPException(status_code=404, detail="Blog not found")
    return await generator.edit_blog(db, blog, payload.instruction)


@router.post("/{blog_id}/publish", response_model=BlogResponse)
def publish_blog(blog_id: int, payload: BlogPublishRequest, db: Session = Depends(db_session)):
    blog = db.get(Blog, blog_id)
    if not blog:
        raise HTTPException(status_code=404, detail="Blog not found")
    if payload.publish:
        return generator.publish_blog(db, blog)
    blog.status = "draft"
    blog.published_url = None
    db.commit()
    db.refresh(blog)
    return blog


@router.delete("/{blog_id}")
def delete_blog(blog_id: int, db: Session = Depends(db_session)):
    blog = db.get(Blog, blog_id)
    if not blog:
        raise HTTPException(status_code=404, detail="Blog not found")
    from app.config import get_settings

    settings = get_settings()
    generated_dir = settings.generated_blogs_path / blog.slug
    uploads_dir = settings.uploads_path / blog.slug
    blog.current_version = None
    blog.current_version_id = None
    db.flush()
    db.execute(delete(BlogMessage).where(BlogMessage.blog_id == blog_id))
    db.execute(delete(PromptHistory).where(PromptHistory.blog_id == blog_id))
    db.execute(delete(BlogImage).where(BlogImage.blog_id == blog_id))
    db.execute(delete(BlogVersion).where(BlogVersion.blog_id == blog_id))
    db.delete(blog)
    db.commit()
    for target in (generated_dir, uploads_dir):
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
    return {"deleted": True, "blog_id": blog_id}


@router.post("/{blog_id}/images")
async def upload_image(blog_id: int, file: UploadFile = File(...), db: Session = Depends(db_session)):
    blog = db.get(Blog, blog_id)
    if not blog:
        raise HTTPException(status_code=404, detail="Blog not found")
    from app.config import get_settings

    settings = get_settings()
    stored_path, analysis = await image_service.save_upload(blog.slug, settings.uploads_path, file)
    record = BlogImage(
        blog_id=blog.id,
        original_name=Path(file.filename or stored_path.name).name,
        stored_path=str(stored_path),
        mime_type=file.content_type or "application/octet-stream",
        size_bytes=stored_path.stat().st_size,
        analysis_json=analysis,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"id": record.id, "analysis": analysis}


@router.get("/{blog_id}/export")
def export_blog(blog_id: int, db: Session = Depends(db_session)):
    blog = db.get(Blog, blog_id)
    if not blog or not blog.current_version:
        raise HTTPException(status_code=404, detail="Blog not found")
    data = export_service.build_zip(blog, blog.current_version)
    filename = f"{blog.slug}.zip"
    return StreamingResponse(iter([data]), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
