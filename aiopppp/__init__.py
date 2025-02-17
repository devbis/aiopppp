import asyncio
from typing import Callable

from .discover import Discovery  # noqa: F401
from .session import JsonSession, Session  # noqa: F401
from .types import Device


async def connect(ip_address: str, timeout: int = 20) -> Device:
    """Connect to the camera."""
    loop = asyncio.get_running_loop()
    cam_device_fut = loop.create_future()

    def on_device_connect(device):
        cam_device_fut.set_result(device)

    discovery = Discovery(ip_address)
    await asyncio.wait(
        [
            loop.create_task(discovery.discover(on_device_connect)),
            cam_device_fut,
        ],
        timeout=timeout,
        return_when=asyncio.FIRST_COMPLETED,
    )
    if cam_device_fut.done():
        return cam_device_fut.result()
    raise TimeoutError("Timeout connecting to the camera")


def make_session(device: Device, on_device_lost: Callable[[Device], None]) -> Session:
    """Create a session for the camera."""
    if device.is_json:
        return JsonSession(device, on_disconnect=on_device_lost)
    raise NotImplementedError("Only JSON protocol is supported")
