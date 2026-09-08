import hashlib


def make_id(*parts: str) -> str:
    digest = hashlib.sha256("::".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return digest[:32]
