from __future__ import annotations

from pathlib import Path

from fastapi import UploadFile


class ImageService:
    async def save_upload(self, blog_slug: str, upload_root: Path, upload: UploadFile) -> tuple[Path, dict]:
        safe_name = Path(upload.filename or "image.bin").name
        target_dir = upload_root / blog_slug
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / safe_name
        content = await upload.read()
        target_path.write_bytes(content)
        analysis = self.analyze_bytes(content, safe_name)
        return target_path, analysis

    def analyze_bytes(self, content: bytes, filename: str) -> dict:
        checksum = sum(content[:1024]) if content else 0
        return {
            "filename": filename,
            "style": "editorial clean",
            "dominant_colors": ["#14532d", "#f59e0b", "#f8fafc"],
            "layout": "hero with content grid",
            "approx_typography": "sans-serif display + readable body",
            "checksum_hint": checksum,
        }
