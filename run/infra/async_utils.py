"""Small asyncio bridges for synchronous runtime workers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar


_T = TypeVar("_T")


def run_awaitable(value: Awaitable[_T]) -> _T:
    """Await a tool result in the worker's new event loop.

    Python 3.10–3.13 require a coroutine, not an arbitrary awaitable, at the
    asyncio.run boundary.  This wrapper does not migrate loop-bound objects.
    """

    async def await_result() -> _T:
        return await value

    return asyncio.run(await_result())
