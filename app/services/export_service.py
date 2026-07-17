from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from app.models import Blog, BlogVersion


class ExportService:
    def build_zip(self, blog: Blog, version: BlogVersion) -> bytes:
        stream = BytesIO()
        with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("index.html", version.html_content)
            archive.writestr("assets/style.css", version.css_content)
            archive.writestr("assets/app.js", version.js_content)
            archive.writestr("meta.json", str(version.seo_metadata))
            archive.writestr(
                "README.txt",
                f"Blog: {blog.title}\nVersion: {version.version_number}\nStatus: {blog.status}\n",
            )
        return stream.getvalue()
