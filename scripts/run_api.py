"""Start the Dispute Desk API with a Windows-compatible asyncio loop."""

from __future__ import annotations

import asyncio
import selectors
import sys


async def _serve() -> None:
    import uvicorn

    config = uvicorn.Config(
        "src.api.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    await server.serve()


def main() -> None:
    if sys.platform == "win32":
        asyncio.run(
            _serve(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        asyncio.run(_serve())


if __name__ == "__main__":
    main()
