import hashlib


class VectorService:
    def embed_text(self, text: str, size: int = 768) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values = []
        for index in range(size):
            byte = digest[index % len(digest)]
            values.append((byte / 255.0) * 2 - 1)
        return values
