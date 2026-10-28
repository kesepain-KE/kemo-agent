"""Declare native media input; only the owning runtime may enqueue it."""

from typing import Any

from run.extensions import request_media_attachment


def run(
    asset_ids: list[str] | None = None,
    paths: list[str] | None = None,
    note: str = "",
    detail: str = "auto",
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del context
    return request_media_attachment(
        asset_ids=asset_ids, paths=paths, note=note, detail=detail
    )
