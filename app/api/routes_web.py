from fastapi import APIRouter, Depends, HTTPException, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.dependencies import db_session
from app.models import Blog, BlogVersion
from app.services.auth_service import AuthService


router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
auth_service = AuthService()


def _session_username(request: Request) -> str | None:
    return request.session.get("user")


def _redirect_to_login() -> RedirectResponse:
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/", response_class=HTMLResponse)
def root(request: Request):
    if _session_username(request):
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if not auth_service.verify_admin_credentials(username, password):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Credenciales invalidas"}, status_code=401)
    request.session["user"] = username
    request.session["csrf_token"] = auth_service.issue_csrf_token()
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(db_session)):
    username = _session_username(request)
    if not username:
        return _redirect_to_login()
    total_blogs = db.scalar(select(func.count(Blog.id))) or 0
    published_blogs = db.scalar(select(func.count(Blog.id)).where(Blog.status == "published")) or 0
    total_versions = db.scalar(select(func.count(BlogVersion.id))) or 0
    recent_blogs = list(db.scalars(select(Blog).order_by(Blog.updated_at.desc()).limit(8)).all())
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "username": username,
            "total_blogs": total_blogs,
            "published_blogs": published_blogs,
            "total_versions": total_versions,
            "recent_blogs": recent_blogs,
            "csrf_token": request.session.get("csrf_token"),
        },
    )


@router.get("/blogs/{blog_id}", response_class=HTMLResponse)
def blog_detail(request: Request, blog_id: int, db: Session = Depends(db_session)):
    username = _session_username(request)
    if not username:
        return _redirect_to_login()
    blog = db.get(Blog, blog_id)
    if not blog:
        raise HTTPException(status_code=404, detail="Blog not found")
    return templates.TemplateResponse(
        "blog_detail.html",
        {"request": request, "username": username, "blog": blog, "csrf_token": request.session.get("csrf_token")},
    )


@router.get("/preview/{slug}", response_class=HTMLResponse)
def preview_blog(slug: str, db: Session = Depends(db_session)):
    blog = db.scalar(select(Blog).where(Blog.slug == slug))
    if not blog or not blog.current_version:
        raise HTTPException(status_code=404, detail="Blog not found")
    return HTMLResponse(blog.current_version.html_content)


@router.get("/generated/{slug}/{asset_name}")
def generated_asset(slug: str, asset_name: str):
    from app.config import get_settings

    settings = get_settings()
    target = settings.generated_blogs_path / slug / asset_name
    if not target.exists():
        raise HTTPException(status_code=404, detail="Asset not found")
    media_type = "text/plain"
    if asset_name.endswith(".css"):
        media_type = "text/css"
    elif asset_name.endswith(".js"):
        media_type = "application/javascript"
    elif asset_name.endswith(".html"):
        media_type = "text/html"
    return Response(content=target.read_text(encoding="utf-8"), media_type=media_type)
