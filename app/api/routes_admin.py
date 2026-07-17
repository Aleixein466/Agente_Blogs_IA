from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies import db_session, require_csrf, require_session_user
from app.models import AgentLog, Blog, PromptHistory


router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_session_user)])


@router.get("/summary")
def summary(db: Session = Depends(db_session)):
    blogs = list(db.scalars(select(Blog).order_by(Blog.created_at.desc()).limit(20)).all())
    prompts = list(db.scalars(select(PromptHistory).order_by(PromptHistory.created_at.desc()).limit(20)).all())
    logs = list(db.scalars(select(AgentLog).order_by(AgentLog.created_at.desc()).limit(20)).all())
    return {
        "blogs": [{"id": blog.id, "title": blog.title, "status": blog.status} for blog in blogs],
        "prompts": [{"id": prompt.id, "type": prompt.prompt_type, "text": prompt.prompt_text} for prompt in prompts],
        "logs": [{"id": log.id, "task_type": log.task_type, "status": log.status} for log in logs],
    }


@router.post("/csrf-check", dependencies=[Depends(require_csrf)])
def csrf_check(request: Request):
    return {"ok": True, "user": request.session.get("user")}
