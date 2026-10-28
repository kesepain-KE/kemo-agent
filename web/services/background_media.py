"""Bounded validation of image data and video containers, not codec transcoding."""

from __future__ import annotations

import io
import warnings
from PIL import Image

from web.errors import InvalidRequestError

BACKGROUND_IMAGE_MAX_BYTES = 100 * 1024 * 1024
BACKGROUND_MAX_BYTES = 400 * 1024 * 1024
BACKGROUND_FORMATS = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".mp4": "video/mp4", ".webm": "video/webm",
}


def _mp4_boxes(data: bytes | memoryview):
    # Container payloads may now be 400 MB; don't copy each mdat/segment slice.
    data = memoryview(data)
    offset, count = 0, 0
    while offset < len(data):
        count += 1
        if count > 100_000:
            raise ValueError("too many boxes")
        if len(data) - offset < 8:
            raise ValueError("truncated box")
        size = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        header = 8
        if size == 1:
            if len(data) - offset < 16:
                raise ValueError("truncated extended box")
            size = int.from_bytes(data[offset + 8:offset + 16], "big")
            header = 16
        if size == 0:
            size = len(data) - offset
        if size < header or offset + size > len(data):
            raise ValueError("invalid box")
        yield kind, data[offset + header:offset + size]
        offset += size


def _is_mp4(data: bytes) -> bool:
    boxes = list(_mp4_boxes(data))
    ftyp = next((value for kind, value in boxes if kind == b"ftyp"), b"")
    if not 8 <= len(ftyp) <= 4096 or len(ftyp) % 4:
        return False
    brands = {ftyp[:4], *(ftyp[i:i + 4] for i in range(8, len(ftyp), 4))}
    if not brands.intersection({b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V ", b"dash"}):
        return False
    video = False
    for kind, movie in boxes:
        if kind != b"moov":
            continue
        for track_kind, track in _mp4_boxes(movie):
            if track_kind != b"trak":
                continue
            for media_kind, media in _mp4_boxes(track):
                if media_kind == b"mdia":
                    video |= any(k == b"hdlr" and v[8:12] == b"vide" for k, v in _mp4_boxes(media))
    return video and any(kind == b"mdat" and value for kind, value in boxes)


def _vint(data: bytes | memoryview, offset: int, *, identifier: bool = False):
    if offset >= len(data) or data[offset] == 0:
        raise ValueError("invalid EBML integer")
    length = 1
    mask = 0x80
    while not data[offset] & mask:
        length += 1
        mask >>= 1
    if length > (4 if identifier else 8) or offset + length > len(data):
        raise ValueError("invalid EBML length")
    raw = int.from_bytes(data[offset:offset + length], "big")
    value = raw if identifier else raw & ((1 << (7 * length)) - 1)
    unknown = not identifier and value == (1 << (7 * length)) - 1
    return value, offset + length, unknown


def _ebml_elements(data: bytes | memoryview):
    data = memoryview(data)
    offset, count = 0, 0
    while offset < len(data):
        count += 1
        if count > 100_000:
            raise ValueError("too many elements")
        kind, offset, _ = _vint(data, offset, identifier=True)
        size, offset, unknown = _vint(data, offset)
        end = len(data) if unknown else offset + size
        if end > len(data):
            raise ValueError("truncated EBML")
        yield kind, data[offset:end]
        offset = end


def _is_webm(data: bytes) -> bool:
    elements = list(_ebml_elements(data))
    if not elements or elements[0][0] != 0x1A45DFA3:
        return False
    if not any(k == 0x4282 and v == b"webm" for k, v in _ebml_elements(elements[0][1])):
        return False
    segment = next((value for kind, value in elements if kind == 0x18538067), b"")
    video, cluster = False, False
    for kind, value in _ebml_elements(segment):
        if kind == 0x1F43B675:
            cluster |= bool(value)
        if kind == 0x1654AE6B:
            for track_kind, track in _ebml_elements(value):
                if track_kind == 0xAE:
                    video |= any(k == 0x83 and v == b"\x01" for k, v in _ebml_elements(track))
    return video and cluster


def validate_background(data: bytes) -> tuple[str, str]:
    if not data or len(data) > BACKGROUND_MAX_BYTES:
        raise InvalidRequestError("背景文件不能为空且不能超过 400 MB")
    try:
        if data.startswith((b"\xff\xd8", b"\x89PNG", b"RIFF")):
            if len(data) > BACKGROUND_IMAGE_MAX_BYTES:
                raise ValueError("image exceeds limit")
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(image.format or "")
                    if not suffix or image.width * image.height > 32_000_000 or getattr(image, "is_animated", False):
                        raise ValueError("unsupported image")
                    image.verify()
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
            return suffix, BACKGROUND_FORMATS[suffix]
        if data[4:8] == b"ftyp" and _is_mp4(data):
            return ".mp4", "video/mp4"
        if data.startswith(b"\x1aE\xdf\xa3") and _is_webm(data):
            return ".webm", "video/webm"
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        pass
    raise InvalidRequestError("背景仅支持有效的 JPG、PNG、WebP 图片（100 MB 内）或含视频轨的 MP4、WebM（400 MB 内）")
