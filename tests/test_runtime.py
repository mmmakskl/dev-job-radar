import asyncio

from tg_vacancy_bot.runtime import wait_for_disconnect_or_shutdown


def test_shutdown_event_leaves_client_connected_for_queue_drain() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.connected = True
            self.disconnected = asyncio.Event()

        def is_connected(self) -> bool:
            return self.connected

        async def run_until_disconnected(self) -> None:
            await self.disconnected.wait()

    async def scenario() -> FakeClient:
        client = FakeClient()
        shutdown_event = asyncio.Event()
        shutdown_event.set()
        return client, await wait_for_disconnect_or_shutdown(client, shutdown_event)

    client, shutdown_requested = asyncio.run(scenario())
    assert shutdown_requested
    assert client.connected
