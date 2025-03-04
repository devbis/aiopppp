import asyncio
import contextlib

from .discover import Discovery
from .exceptions import AlreadyConnectedError, NotConnectedError
from .session import Session, make_session
from .types import DeviceDescriptor


async def find_device(ip_address: str, timeout: int = 20) -> DeviceDescriptor:
    """Connect to the camera."""
    loop = asyncio.get_running_loop()
    cam_device_fut = loop.create_future()

    def on_device_connect(device):
        if not cam_device_fut.done():
            cam_device_fut.set_result(device)

    discovery = Discovery(ip_address)
    task = loop.create_task(discovery.discover(on_device_connect, period=1))
    await asyncio.wait(
        [
            task,
            cam_device_fut,
        ],
        timeout=timeout,
        return_when=asyncio.FIRST_COMPLETED,
    )
    if not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    if cam_device_fut.done():
        return cam_device_fut.result()
    raise TimeoutError("Timeout connecting to the camera")


class Device:
    def __init__(self, ip_address: str, username: str = '', password: str = ''):
        self.ip_address = ip_address
        self.descriptor: DeviceDescriptor | None = None
        self.properties: dict = {}
        self._session: Session | None = None
        self.username = username
        self.password = password
        self.enable_reconnect = False

    async def find_device(self, timeout: int = 15):
        self.descriptor = await find_device(self.ip_address, timeout)
        return self.descriptor

    async def connect(self, timeout: int = 15):
        if self.is_connected:
            raise AlreadyConnectedError("Already connected to the camera")

        await self.find_device(timeout=timeout)

        self._session = make_session(
            device=self.descriptor,
            login=self.username,
            password=self.password,
            on_device_lost=lambda dev: self.on_device_lost(),
        )
        self._session.start()
        try:
            await asyncio.wait(
                [
                    asyncio.ensure_future(self._session.device_is_ready.wait()),
                    asyncio.shield(self._session.main_task),
                ], timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if self._session and self._session.main_task and self._session.main_task.done():
                await self._session.main_task
        except asyncio.TimeoutError:
            if self.is_connected:
                await self.close()
            raise TimeoutError("Timeout connecting to the camera")
        if not self.is_connected:
            # usually, device didn't respond to login/get_settings commands in time
            raise NotConnectedError("Device lost during connection")

        if self.session.dev_properties:
            self.properties = self.session.dev_properties

    def on_device_lost(self):
        # session is closed here
        self._session = None
        if self.enable_reconnect:
            # TODO
            pass
            # await self.find_device(timeout=timeout)

    @property
    def is_connected(self):
        return bool(self._session)

    @property
    def session(self):
        if not self._session:
            raise NotConnectedError("Not connected to the camera")
        return self._session

    async def close(self):
        if self._session:
            await self._session.send_close_pkt()
            sess = self._session
            self._session.stop()
            self._session = None

            if sess.main_task:
                try:
                    await sess.main_task
                except asyncio.CancelledError:
                    pass

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    @property
    def is_video_requested(self):
        return self.session.is_video_requested

    async def start_video(self):
        return await self.session.start_video()

    async def stop_video(self):
        return await self.session.stop_video()

    async def get_video_frame(self):
        if not self.session:
            raise NotConnectedError("Not connected to the camera")
        frame = await self.session.frame_buffer.get()
        if not frame:
            raise NotConnectedError("Not connected to the camera")
        return frame

    async def reboot(self):
        return await self.session.reboot()
