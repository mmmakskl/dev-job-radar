import asyncio

from tg_vacancy_bot.runtime import wait_for_disconnect_or_shutdown


def test_shutdown_event_disconnects_client() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.connected = True
            self.disconnected = asyncio.Event()

        def is_connected(self) -> bool:
            return self.connected

        async def disconnect(self) -> None:
            self.connected = False
            self.disconnected.set()

        async def run_until_disconnected(self) -> None:
            await self.disconnected.wait()

    async def scenario() -> FakeClient:
        client = FakeClient()
        shutdown_event = asyncio.Event()
        shutdown_event.set()
        await wait_for_disconnect_or_shutdown(client, shutdown_event)
        return client

    client = asyncio.run(scenario())
    assert not client.connected
