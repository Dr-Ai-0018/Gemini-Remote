"""Shared helpers: a real Gateway served by uvicorn on 127.0.0.1, and polling."""

import asyncio

import uvicorn

from gemini_remote.gateway.app import create_app

CONTROL, DEVICE, SIGNING = "c" * 40, "d" * 40, "s" * 40
RESULT = {"text": "done", "documents": [], "truncated": False}


async def until(predicate, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


class Gateway:
    """The real Gateway app; restartable on the same port with the same store."""

    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.port = 0

    async def start(self):
        app = create_app(self.settings, store=self.store, dispatch_interval=0.02)
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning",
                                ws="websockets-sansio", lifespan="on")
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve())
        await until(lambda: self.server.started or self.task.done())
        self.port = self.server.servers[0].sockets[0].getsockname()[1]

    async def stop(self):
        self.server.should_exit = True
        await self.task
