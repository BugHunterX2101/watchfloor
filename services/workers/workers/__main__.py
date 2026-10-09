"""All local workers in one process: ``python -m workers`` (Procfile.dev).

- dispatcher: polls the outbox every 2 s (there is no SQS locally);
- sweeper: every 60 s;
- watcher: every 15 min, first run at start.

``--once`` runs the watcher, the sweeper and one dispatcher pass, then exits.
The canary is not scheduled locally: run ``python -m workers.canary`` against
a running stack.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from . import dispatcher, runtime, sweeper, watcher

log = logging.getLogger("watchfloor.workers")


async def _every(seconds: float, name: str, job: Callable[[], Awaitable[Any]]) -> None:
    while True:
        try:
            await job()
        except Exception:
            log.exception("%s run failed", name)
        await asyncio.sleep(seconds)


async def _main(opts: argparse.Namespace) -> None:
    await runtime.ensure_pool(max_size=5)
    deps = dispatcher.Deps.from_settings()
    if opts.once:
        print("watcher   ", await watcher.run())
        print("sweeper   ", await sweeper.run())
        print("dispatcher", dict(await dispatcher.drain(deps)))
        return
    log.info(
        "workers up: dispatcher every %ss, sweeper every %ss, watcher every %ss",
        opts.poll_every,
        opts.sweep_every,
        opts.watch_every,
    )
    await asyncio.gather(
        dispatcher.poll(deps, every_s=opts.poll_every),
        _every(opts.sweep_every, "sweeper", sweeper.run),
        _every(opts.watch_every, "watcher", watcher.run),
    )


def main(argv: list[str] | None = None) -> int:
    runtime.configure_logging()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--poll-every", type=float, default=2.0)
    ap.add_argument("--sweep-every", type=float, default=60.0)
    ap.add_argument("--watch-every", type=float, default=900.0)
    asyncio.run(_main(ap.parse_args(argv)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
