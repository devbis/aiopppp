import asyncio
import logging

from .discover import Discovery
from .session import JsonSession


def on_device_found(device):
    session = JsonSession(device)
    session.start()

async def amain():
    discovery = Discovery()
    await discovery.discover(on_device_found)


def main():
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(amain())

if __name__ == '__main__':
    main()
