from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db


def db_session(db: Session = Depends(get_db)) -> Session:
    return db


def require_session_user(request: Request) -> str:
    username = request.session.get("user")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return username


def require_csrf(request: Request) -> None:
    session_token = request.session.get("csrf_token")
    header_token = request.headers.get("X-CSRF-Token") or request.query_params.get("csrf_token")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and session_token != header_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
