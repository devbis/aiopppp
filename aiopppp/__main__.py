import argparse
import asyncio
import logging

from .discover import DEFAULT_DISCOVERY_ADDRESS, Discovery
from .http_server import SESSIONS, start_web_server
from .session import make_session

logger = logging.getLogger(__name__)

discovery = None


def on_device_found(device, login, password):
    session = make_session(device, on_device_lost=on_device_lost, login=login, password=password)
    SESSIONS[device.dev_id.dev_id] = session
    session.start()


def on_device_lost(device):
    logger.warning('Device %s lost', device.dev_id)
    SESSIONS.pop(device.dev_id.dev_id, None)


async def amain(remote_addr, local_port, username, password):
    global discovery
    discovery = Discovery(remote_addr=remote_addr)
    try:
        await asyncio.gather(discovery.discover(lambda d: on_device_found(d, username, password)), start_web_server())
    finally:
        for dev_id, session in list(SESSIONS.items()):
            session.stop()
        SESSIONS.clear()


def main():
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
    parser.add_argument(
        '--log-level',
        type=str,
        default='INFO',
    )
    parser.add_argument(
        '-u',
        '--username',
        type=str,
        default='',
        help='Auth login',
    )
    parser.add_argument(
        '-p',
        '--password',
        type=str,
        default='',
        help='Auth password',
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.getLevelName(args.log_level.upper()))
    asyncio.run(amain(
        remote_addr=args.addr,
        local_port=args.local_discovery_port,
        username=args.username,
        password=args.password,
    ))


if __name__ == '__main__':
    main()
