"""UI projections of validated native media; never expose arbitrary local paths."""
from pathlib import Path
from typing import Any

from run.extensions.attachments import history_attachment_descriptors, _is_link_or_junction


def public_media_assets(values: list[dict[str, Any]], *, root: Path, user: str) -> list[dict[str, Any]]:
    result = []
    for raw in values[:32]:
        projected = history_attachment_descriptors([raw])
        if not projected:
            continue
        item = projected[0]
        item.update(scope="external", relative_path="", available=False)
        path = None
        raw_path = raw.get("path")
        if isinstance(raw_path, str) and raw_path:
            path = Path(raw_path)
            if not path.is_absolute():
                path = root / path
        elif isinstance(raw.get("scope"), str) and raw["scope"] in {"download", "file_upload"} and raw.get("relative_path"):
            path = root / "users" / user / str(raw["scope"]) / str(raw["relative_path"])
        if path is not None:
            try:
                if any(_is_link_or_junction(part) for part in (path, *path.parents)):
                    raise ValueError("linked media")
                path = path.resolve()
                own = (root / "users" / user).resolve()
                for area in ("download", "file_upload"):
                    base = own / area
                    if not path.is_relative_to(base):
                        continue
                    relative = path.relative_to(base).as_posix()
                    if relative == ".":
                        break
                    item.update(scope=area, relative_path=relative)
                    info = path.stat()
                    item["available"] = path.is_file() and info.st_nlink == 1 and info.st_size == item["size"]
                    break
            except (OSError, ValueError):
                pass
        # External/message/private paths stay descriptive only, not downloadable.
        if item["scope"] not in {"download", "file_upload"}:
            item.update(scope="external", relative_path="", available=False)
        result.append(item)
    return result
