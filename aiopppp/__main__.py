import asyncio
import logging

from .discover import Discovery
from .http_server import start_web_server, SESSIONS
from .session import JsonSession

logger = logging.getLogger(__name__)


def on_device_found(device):
    session = JsonSession(device)
    SESSIONS[device.dev_id.dev_id] = session
    session.start()


def on_device_lost(device):
    # TODO: add event
    SESSIONS.pop(device.dev_id.dev_id, None)


async def amain():
    discovery = Discovery()
    await asyncio.gather(discovery.discover(on_device_found), start_web_server())


def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(amain())


if __name__ == '__main__':
    main()
