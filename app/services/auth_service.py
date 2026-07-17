from secrets import token_urlsafe

from passlib.context import CryptContext

from app.config import get_settings


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def verify_admin_credentials(self, username: str, password: str) -> bool:
        return username == self.settings.admin_username and password == self.settings.admin_password

    def issue_csrf_token(self) -> str:
        return token_urlsafe(32)
