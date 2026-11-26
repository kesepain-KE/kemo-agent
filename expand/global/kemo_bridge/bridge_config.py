"""Identity and public configuration of this bridge instance."""
import json
from pathlib import Path
from urllib.parse import urlsplit

BASE_DIR = Path(__file__).resolve().parent
USERS_DIR = BASE_DIR.parents[2] / "users"
SERVICE_ID = "kemo_bridge"
SERVICE_NAME = "kemo-bridge"
VERSION = "2.1.0"
INTERNAL_HEADER = "X-Kemo-Internal"
PEER_CHAT_PATH = "/peer/v1/chat"
PEER_RUN_HEADER = "X-Kemo-Run-Id"
PEER_ACK_HEADER = "X-Kemo-Chat-Ack"
PEER_RUN_PATTERN = r"^[A-Za-z0-9_-]+$"
MAX_EVENT_ID = 2**63 - 1
PEER_PAGE_BYTES = 1024 * 1024
PEER_FRAME_BYTES = PEER_PAGE_BYTES - 512  # Leave room for the page cursor and terminal summary.
PEER_RETENTION_DAYS = 30


def peer_sse(value: dict, *, event_id: int | None = None) -> bytes:
    header = "" if event_id is None else f"id: {event_id}\n"
    return f"{header}data: {json.dumps(value, ensure_ascii=False)}\n\n".encode()


def normalize_public_url(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("invalid_public_url")
    url = value.strip()
    if not url:
        return None
    if any(character.isspace() or ord(character) < 32 for character in url) or "\\" in url:
        raise ValueError("invalid_public_url")
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"} or not parts.hostname
        or parts.username is not None or parts.password is not None
        or parts.query or parts.fragment or parts.port == 0
    ):
        raise ValueError("invalid_public_url")
    return url.rstrip("/")


def public_url(config: dict) -> str | None:
    settings = config.get("bridge")
    return normalize_public_url(settings.get("public_url")) if isinstance(settings, dict) else None
