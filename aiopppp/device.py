import asyncio
import contextlib
import logging

from .discover import Discovery
from .exceptions import AlreadyConnectedError, NotConnectedError
from .session import Session, make_session
from .types import DeviceDescriptor

logger = logging.getLogger(__name__)


async def find_device(ip_address: str, timeout: int = 20) -> DeviceDescriptor:
    """Connect to the camera."""
    loop = asyncio.get_running_loop()
    cam_device_fut = loop.create_future()

    def on_device_connect(device):
        if not cam_device_fut.done():
            cam_device_fut.set_result(device)

    discovery = Discovery(ip_address)
    task = loop.create_task(discovery.discover(on_device_connect, period=1))
    try:
        await asyncio.wait(
            [
                task,
                cam_device_fut,
            ],
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    if cam_device_fut.done():
        return cam_device_fut.result()
    raise TimeoutError("Timeout connecting to the camera")


class Device:
    def __init__(self, ip_address: str, username: str = '', password: str = '',
                 on_video_state_change=None):
        self.ip_address = ip_address
        self.descriptor: DeviceDescriptor | None = None
        self.properties: dict = {}
        self._session: Session | None = None
        self.username = username
        self.password = password
        # Optional callback(is_streaming: bool) forwarded to the session, fired
        # whenever video streaming starts or stops.
        self.on_video_state_change = on_video_state_change
        self.enable_reconnect = False
        # Auto-reconnect bookkeeping.
        self._reconnect_task = None
        self._closing = False
        # Whether the caller wants video, so a reconnect can resume streaming.
        self._want_video = False
        # Backoff bounds for reconnect attempts (seconds).
        self.reconnect_min_delay = 1
        self.reconnect_max_delay = 30

    async def connect(self, timeout: int = 15):
        if self.is_connected:
            raise AlreadyConnectedError("Already connected to the camera")
        # Allow reuse of a Device that was previously close()d.
        self._closing = False

        self.descriptor = await find_device(self.ip_address, timeout=timeout)

        self._session = make_session(
            device=self.descriptor,
            login=self.username,
            password=self.password,
            on_device_lost=lambda dev: self.on_device_lost(),
            on_video_state_change=self.on_video_state_change,
        )
        self._session.start()
        session_tasks = self._session.running_tasks()
        done, _ = await asyncio.wait(
            [
                asyncio.ensure_future(self._session.device_is_ready.wait()),
                *[asyncio.shield(t) for t in session_tasks],
            ], timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if self._session:
            # if exception in session tasks due to packets processing, raise it here
            done_tasks = [t for t in session_tasks if t.done()]
            if done_tasks:
                await asyncio.gather(*done_tasks)
        if not done:  # timeout
            if self.is_connected:
                await self.close()
            raise TimeoutError("Timeout connecting to the camera")
        if not self.is_connected:
            # usually, device didn't respond to login/get_settings commands in time
            raise NotConnectedError("Device lost during connection")

        if self.session.dev_properties:
            # {
            #     'tz': -3,
            #     'time': 3950400351,
            #     'icut': 0,
            #     'batValue': 90,
            #     'batStatus': 1,
            #     'sysver': 'HQLS_HQT66DP_20240925 11:06:42',
            #     'mcuver': '1.1.1.1',
            #     'sensor': 'GC0329',
            #     'isShow4KMenu': 0,
            #     'isShowIcutAuto': 1,
            #     'rotmir': 0,
            #     'signal': 100,
            #     'lamp': 1,
            # }
            self.properties = self.session.dev_properties

    def on_device_lost(self):
        # session is closed here
        self._session = None
        if self.enable_reconnect and not self._closing:
            if self._reconnect_task is None or self._reconnect_task.done():
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    return
                self._reconnect_task = loop.create_task(self._reconnect_loop())

    async def _reconnect_loop(self):
        """Re-establish the session after an unexpected loss, with exponential
        backoff, resuming video if it was streaming."""
        delay = self.reconnect_min_delay
        while self.enable_reconnect and not self._closing and not self.is_connected:
            try:
                await asyncio.sleep(delay)
                if self._closing:
                    return
                await self.connect()
                logger.info('Reconnected to %s', self.ip_address)
                if self._want_video:
                    await self.start_video()
                return
            except asyncio.CancelledError:
                raise
            except Exception as err:
                logger.debug('Reconnect to %s failed (%s); retrying in %ss',
                             self.ip_address, err, delay)
                delay = min(delay * 2, self.reconnect_max_delay)

    @property
    def is_connected(self):
        return bool(self._session)

    @property
    def session(self):
        if not self._session:
            raise NotConnectedError("Not connected to the camera")
        return self._session

    async def close(self):
        # Stop any in-flight reconnect first so it can't race a deliberate close.
        self._closing = True
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
        self._reconnect_task = None

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
        self._want_video = True
        return await self.session.start_video()

    async def stop_video(self):
        self._want_video = False
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
