import argparse
import asyncio
import logging

from .discover import DEFAULT_DISCOVERY_ADDRESS, Discovery
from .http_server import SESSIONS, start_web_server
from .session import JsonSession

logger = logging.getLogger(__name__)

discovery = None


def on_device_found(device):
    session = JsonSession(device, on_disconnect=on_device_lost)
    SESSIONS[device.dev_id.dev_id] = session
    session.start()


def on_device_lost(device):
    logger.warning('Device %s lost', device.dev_id)
    s = SESSIONS.pop(device.dev_id.dev_id, None)
    if s:
        s.main_task.cancel()
    if discovery:
        discovery.delete_device(device)


async def amain(remote_addr, local_port):
    global discovery
    discovery = Discovery(remote_addr=remote_addr)
    await asyncio.gather(discovery.discover(on_device_found), start_web_server())


def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(
        prog='aiopppp',
        description='A test web server to serve video stream from PPPP-based cameras',
    )
    parser.add_argument(
        '-a',
        '--addr',
        type=str,
        default=DEFAULT_DISCOVERY_ADDRESS,
        help=f'Remote discovery address, default is {DEFAULT_DISCOVERY_ADDRESS}',
    )
    parser.add_argument(
        '-dp',
        '--local-discovery-port',
        type=int,
        default=0,
        help='Local discovery port for receiving incoming discovery packets, default is random',
    )
    args = parser.parse_args()
    asyncio.run(amain(remote_addr=args.addr, local_port=args.local_discovery_port))


if __name__ == '__main__':
    main()
