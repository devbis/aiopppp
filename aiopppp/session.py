import asyncio
import datetime
import logging
import struct
from enum import Enum
from typing import Callable

from .const import (
    JSON_COMMAND_NAMES,
    PTZ,
    BinaryCommands,
    JsonCommands,
    PacketType,
    PtzDirection,
    PtzParamType,
    VideoParamType,
    VideoResolution,
)
from .encrypt import ENC_METHODS
from .exceptions import AuthError, CommandResultError
from .packets import (
    BinaryCmdPkt,
    JsonCmdPkt,
    make_close_pkt,
    make_drw_ack_pkt,
    make_p2palive_ack_pkt,
    make_p2palive_pkt,
    make_punch_pkt,
    pack_passtrough_cmd,
    parse_dev_status,
    parse_packet,
)
from .types import Channel, DeviceDescriptor, VideoFrame
from .utils import DebounceEvent

logger = logging.getLogger(__name__)


class State(Enum):
    DISCONNECTED = 0
    CONNECTED = 1
    READY = 2


class SessionUDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_receive):
        super().__init__()
        self.on_receive = on_receive

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.on_receive(data)


class PacketQueueMixin:
    def __init__(self, *args, **kwargs):
        super().__init__()
        self.packet_queue = asyncio.Queue()
        self.process_packet_task = None

    async def process_packet_queue(self):
        while True:
            pkt = await self.packet_queue.get()
            await self.handle_incoming_packet(pkt)

    def start_packet_queue(self):
        self.process_packet_task = asyncio.create_task(self.process_packet_queue())

    async def handle_incoming_packet(self, pkt):
        raise NotImplementedError


class VideoQueueMixin:
    def __init__(self, *args, **kwargs):
        super().__init__()
        self.video_chunk_queue = asyncio.Queue()
        self.frame_buffer = SharedFrameBuffer()
        self.process_video_task = None
        self.last_drw_pkt_idx = 0
        self.video_epoch = 0  # number of overflows over 0xffff DRW index

        self.video_received = {}
        self.video_boundaries = set()
        self.last_video_frame = -1

    async def process_video_queue(self):
        while True:
            pkt_epoch, pkt = await self.video_chunk_queue.get()
            await self.handle_incoming_video_packet(pkt_epoch, pkt)

    def start_video_queue(self):
        self.process_video_task = asyncio.create_task(self.process_video_queue())

    async def handle_incoming_video_packet(self, pkt_epoch, pkt):
        video_payload = pkt.get_drw_payload()
        # logger.info(f'- video frame {pkt._cmd_idx}')
        video_marker = b'\x55\xaa\x15\xa8'  # next \x03 - video marker

        video_chunk_idx = pkt._cmd_idx + 0x10000 * pkt_epoch

        # 0x20 - size of the header starting with this magic
        if video_payload.startswith(video_marker):
            self.video_boundaries.add(video_chunk_idx)
            self.video_received[video_chunk_idx] = video_payload[0x20:]
        else:
            self.video_received[video_chunk_idx] = video_payload
        await self.process_video_frame()

    async def process_video_frame(self):
        if len(self.video_boundaries) <= 1:
            return
        frame_starts = sorted(list(self.video_boundaries))
        index = frame_starts[-2]
        last_index = frame_starts[-1]

        if index == self.last_video_frame:
            return

        complete = True
        out = []
        completeness = ''
        for i in range(index, last_index):
            if self.video_received.get(i) is not None:
                out.append(self.video_received[i])
                completeness += 'x'
            else:
                complete = False
                completeness += '_'
        logger.debug(f".. completeness: {completeness}")

        if complete:
            self.last_video_frame = index

            await self.frame_buffer.publish(VideoFrame(idx=index, data=b''.join(out)))

            to_delete = [idx for idx in self.video_received.keys() if idx < index]
            for idx in to_delete:
                del self.video_received[idx]
            to_delete = [idx for idx in self.video_boundaries if idx < index]
            for idx in to_delete:
                self.video_boundaries.remove(idx)


class Session(PacketQueueMixin, VideoQueueMixin):
    def __init__(self, dev, on_disconnect, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.state = State.DISCONNECTED
        self.dev = dev
        self.dev_properties = {}
        self.outgoing_command_idx = 0
        self.transport = None
        self.device_is_ready = asyncio.Event()
        self.is_video_requested = False
        self.video_stale_at = None
        self.last_alive_pkt_at = datetime.datetime.now()
        self.last_drw_pkt_at = datetime.datetime.now()
        self.on_disconnect = on_disconnect
        self.main_task = None
        self.drw_waiters = {}
        self.cmd_waiters = {}
        self._p2p_rdy_debouncer = DebounceEvent(delay=0.2)

    def __str__(self):
        return f'Session({self.dev.dev_id}) ({self.state.name})'

    async def create_udp(self):
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: SessionUDPProtocol(lambda data: self.on_receive(data)),
            remote_addr=(self.dev.addr, self.dev.port),
        )
        return transport

    def on_receive(self, data):
        decoded = ENC_METHODS[self.dev.encryption][0](data)
        pkt = parse_packet(decoded)
        # logger.debug(f"recv< {pkt} {pkt.get_payload()}")
        logger.debug(f"recv< {pkt.type}, len={len(pkt.get_payload())}")
        self.packet_queue.put_nowait(pkt)

    async def call_with_error_check(self, coro):
        try:
            return await coro
        finally:
            done_tasks = [t for t in self.running_tasks() if t.done()]
            if done_tasks:
                await asyncio.gather(*done_tasks)

    async def send(self, pkt):
        await self.call_with_error_check(self._send(pkt))

    async def _send(self, pkt):
        logger.debug(f"send> {pkt}")
        if pkt.type == PacketType.Drw:
            self.drw_waiters[pkt._cmd_idx] = asyncio.Future()

        encoded_pkt = ENC_METHODS[self.dev.encryption][1](bytes(pkt))
        self.transport.sendto(encoded_pkt, (self.dev.addr, self.dev.port))

    async def send_close_pkt(self):
        await self.send(make_close_pkt())

    async def handle_incoming_packet(self, pkt):
        if pkt.type == PacketType.PunchPkt:
            pass
        if pkt.type == PacketType.P2pRdy:
            await self._p2p_rdy_debouncer.tick()
        elif pkt.type == PacketType.P2PAlive:
            await self.send(make_p2palive_ack_pkt())
        elif pkt.type == PacketType.Drw:
            await self.handle_drw(pkt)
        elif pkt.type == PacketType.DrwAck:
            logger.debug(f'Got DRW ACK {pkt}')
            await self.handle_drw_ack(pkt)
        elif pkt.type == PacketType.P2PAliveAck:
            logger.debug(f'Got P2PAlive ACK {pkt}')
        elif pkt.type == PacketType.Close:
            await self.handle_close(pkt)
        else:
            logger.warning(f'Got UNKNOWN {pkt}')

    async def login(self):
        pass

    async def start_video(self):
        await self.device_is_ready.wait()
        if not self.is_video_requested:
            logger.info('Start video')
            self.last_drw_pkt_at = datetime.datetime.now()
            await self._request_video(1)
            self.is_video_requested = True

    async def stop_video(self):
        if self.is_video_requested:
            self.is_video_requested = False
            self.video_stale_at = None
            self.video_received = {}
            self.video_boundaries = set()
            self.video_epoch = 0
            self.last_video_frame = -1
            while not self.video_chunk_queue.empty():
                self.video_chunk_queue.get_nowait()
            await self._request_video(0)

    async def _request_video(self, mode):
        """
        Mode is 1 for 640x480 or 2 for 320x240
        """
        pass

    async def handle_drw(self, drw_pkt):
        logger.debug('handle_drw(idx=%s, chn=%s)', drw_pkt._cmd_idx, drw_pkt._channel)
        await self.send(make_drw_ack_pkt(drw_pkt))

    async def handle_drw_ack(self, pkt):
        cmd_idx_ack = int.from_bytes(pkt.get_payload()[4:6], 'big')
        logger.debug('handle_drw_ack(idx=%s)', cmd_idx_ack)
        # logger.info('waiters: %s', self.drw_waiters)
        if cmd_idx_ack in self.drw_waiters:
            # logger.info(
            #     'Got ACK for %d, proceed waiters, total waiters: %d', cmd_idx_ack, len(self.drw_waiters),
            # )
            self.drw_waiters[cmd_idx_ack].set_result(pkt)
            await asyncio.sleep(0)
            del self.drw_waiters[cmd_idx_ack]

    async def wait_ack(self, idx, timeout=5):
        return await self.call_with_error_check(self._wait_ack(idx, timeout))

    async def _wait_ack(self, idx, timeout=5):
        if idx is None:
            raise ValueError('Need to provide numeric command index')
        fut = self.drw_waiters.get(idx)
        if fut:
            logger.debug(f'Waiting for ACK for {idx}')
            try:
                await asyncio.wait_for(fut, timeout=timeout)
                logger.debug('wait_ack(idx=%d) complete, waiters: %d', idx, len(self.drw_waiters))
            except asyncio.TimeoutError:
                self.drw_waiters.pop(idx, None)
                raise

    async def handle_close(self, pkt):
        logger.info('%s requested close', self.dev.dev_id)
        self._on_device_lost()

    async def setup_device(self):
        pass

    async def send_initial_packets(self):
        raise NotImplementedError

    async def _run(self):
        self.transport = await self.create_udp()

        # send punch packet
        await self.send_initial_packets()

        try:
            await asyncio.wait_for(self._p2p_rdy_debouncer.wait(), timeout=10)
            logger.info('Connected to %s at %s, json=%s', self.dev.dev_id, self.dev.addr, self.dev.is_json)
            self.state = State.CONNECTED
            try:
                await self.setup_device()
            except asyncio.TimeoutError:
                logger.error('Timeout during device setup')
                await self.send_close_pkt()
                self._on_device_lost()
                return

            while True:
                await self.loop_step()
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            if self.transport:
                logger.debug('Session main task cancelled, sending close packet')
                await self.send_close_pkt()
            raise

    async def loop_step(self):
        logger.debug(f"iterate in Session for {self.dev.dev_id}")
        if (datetime.datetime.now() - self.last_alive_pkt_at).total_seconds() > 10:
            self.last_alive_pkt_at = datetime.datetime.now()
            logger.info('Send P2PAlive')
            await self.send(make_p2palive_pkt())

    def start(self):
        self.device_is_ready.clear()
        self.start_packet_queue()
        self.start_video_queue()
        self.main_task = asyncio.create_task(self._run())
        return self.main_task

    def running_tasks(self):
        return tuple(x for x in (self.main_task, self.process_packet_task, self.process_video_task) if x)

    def _on_device_lost(self):
        logger.warning('Device %s lost', self.dev.dev_id)
        self.stop()
        if self.on_disconnect:
            self.on_disconnect(self.dev)

    def stop(self):
        if self.state != State.CONNECTED:
            raise RuntimeError('Session is not started')
        logger.info('Stopping task for %s', self.dev.dev_id)
        self.device_is_ready.set()
        self.process_packet_task.cancel()
        self.process_video_task.cancel()
        self.main_task.cancel()
        self.transport.close()
        self.transport = None
        self.state = State.DISCONNECTED

    async def reboot(self):
        raise NotImplementedError

    async def set_video_param(self, name, value):
        raise NotImplementedError

class JsonSession(Session):
    """
    Session for JSON-based protocol
    """
    DEFAULT_LOGIN = 'admin'
    DEFAULT_PASSWORD = '6666'

    def __init__(self, *args, login='', password='', **kwargs):
        super().__init__(*args, **kwargs)
        self.auth_login = login or self.DEFAULT_LOGIN
        self.auth_password = password or self.DEFAULT_PASSWORD

    async def send_initial_packets(self):
        await self.send(make_punch_pkt(self.dev.dev_id))

    def get_common_data(self):
        return {
            'user': self.auth_login,
            'pwd': self.auth_password,
            'devmac': '0000'
        }

    async def send_command(self, cmd, *, with_response=False, **kwargs):
        data = {
            'pro': JSON_COMMAND_NAMES[cmd],
            'cmd': cmd.value,
        }
        pkt_idx = self.outgoing_command_idx
        self.outgoing_command_idx += 1
        pkt = JsonCmdPkt(pkt_idx, {**data, **kwargs, **self.get_common_data()})
        if with_response:
            self.cmd_waiters[cmd.value] = asyncio.Future()
        await self.send(pkt)
        return pkt_idx

    async def login(self):
        return await self.send_command(JsonCommands.CMD_CHECK_USER, with_response=True)

    async def _request_video(self, mode):
        logger.info('Request video %s', mode)
        await self.send_command(JsonCommands.CMD_STREAM, video=mode)

    def _get_drw_epoch(self, drw_pkt):
        if self.last_drw_pkt_idx > 0xff00 and drw_pkt._cmd_idx < 0x100:
            return self.video_epoch + 1
        if self.video_epoch and self.last_drw_pkt_idx < 0x100 and drw_pkt._cmd_idx > 0xff00:
            return self.video_epoch - 1
        return self.video_epoch

    async def handle_drw(self, drw_pkt):
        await super().handle_drw(drw_pkt)
        self.last_drw_pkt_at = datetime.datetime.now()

        # # 0x10000 - max number of chunks in one epoch,we need to keep order of chunks
        pkt_epoch = self._get_drw_epoch(drw_pkt)

        if pkt_epoch > self.video_epoch:
            logger.info('Video epoch changed %s -> %s', self.video_epoch, pkt_epoch)
            self.video_epoch = pkt_epoch
            self.last_drw_pkt_idx = drw_pkt._cmd_idx
        elif self.last_drw_pkt_idx < drw_pkt._cmd_idx:
            self.last_drw_pkt_idx = drw_pkt._cmd_idx

        if drw_pkt._channel == Channel.Video:
            # logger.debug(f'Got video data {drw_pkt.get_drw_payload()}')
            if self.video_stale_at:
                logger.warning('Got video data while stale')
                self.video_stale_at = None
            self.video_chunk_queue.put_nowait((pkt_epoch, drw_pkt))
        elif drw_pkt._channel == Channel.Audio:
            pass
        elif drw_pkt._channel == Channel.Command:
            await self.handle_incoming_command_packet(drw_pkt)

    async def handle_incoming_command_packet(self, drw_pkt):
        if isinstance(drw_pkt, JsonCmdPkt):
            response = drw_pkt.json_payload
            if response['cmd'] in self.cmd_waiters:
                # logger.debug('Got awaited response %s', response)
                self.cmd_waiters[response['cmd']].set_result(response)
                del self.cmd_waiters[response['cmd']]

    async def wait_cmd_result(self, cmd, timeout=5):
        return await self.call_with_error_check(self._wait_cmd_result(cmd, timeout))

    async def _wait_cmd_result(self, cmd, timeout=5):
        fut = self.cmd_waiters.get(cmd.value)
        if fut:
            res = await asyncio.wait_for(fut, timeout=timeout)
            logger.debug('Got command result %s', res)
            return res
        return {'result': -1}

    async def setup_device(self):
        idx = await self.login()
        await self.wait_ack(idx)
        auth_result = await self.wait_cmd_result(JsonCommands.CMD_CHECK_USER)
        if auth_result['result'] != 0:
            raise AuthError(f'Login failed: {auth_result}')
        idx = await self.send_command(JsonCommands.CMD_GET_PARMS, with_response=True)
        # logger.debug('Waiting for params ack')
        await self.wait_ack(idx)

        # {
        #     'tz': -3,
        #     'time': 3950165700,
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
        cam_properties = await self.wait_cmd_result(JsonCommands.CMD_GET_PARMS)
        if cam_properties['result'] != 0:
            raise CommandResultError(f'Get properties failed: {cam_properties}')
        for f in ('cmd', 'result'):
            del cam_properties[f]
        self.dev_properties = cam_properties
        logger.info('Camera properties: %s', cam_properties)
        self.device_is_ready.set()

    async def loop_step(self):
        if (
            self.is_video_requested and not self.video_stale_at and
            (datetime.datetime.now() - self.last_drw_pkt_at).total_seconds() > 5
        ):
            self.video_stale_at = self.last_drw_pkt_at
            logger.info('No video for 5 seconds. Re-request video ')
            await self._request_video(1)
        if self.video_stale_at and (datetime.datetime.now() - self.video_stale_at).total_seconds() > 10:
            # camera disconnected
            logger.warning('No video for 10 seconds. Disconnecting')
            await self.send_close_pkt()
            self._on_device_lost()
        await super().loop_step()

    async def control(self, no_ack=False, **kwargs):
        idx = await self.send_command(JsonCommands.CMD_DEV_CONTROL, **kwargs)
        if not no_ack:
            await self.wait_ack(idx)

    async def toggle_lamp(self, value):
        await self.control(lamp=1 if value else 0)

    async def toggle_whitelight(self, value, **kwargs):
        logger.info('%s: toggle white light = %s', self.dev.dev_id, value)
        idx = await self.send_command(JsonCommands.CMD_SET_WHITELIGHT, status=value)
        await self.wait_ack(idx)

    async def toggle_ir(self, value):
        logger.info('%s: toggle IR = %s', self.dev.dev_id, value)
        idx = await self.control(icut=1 if value else 0)
        await self.wait_ack(idx)

    async def rotate_start(self, value):
        logger.info('%s: rotate_start %s', self.dev.dev_id, value)
        value = PTZ[f'{value.upper()}_START'].value
        idx = await self.send_command(JsonCommands.CMD_PTZ_CONTROL, parms=0, value=value)
        await self.wait_ack(idx)

    async def rotate_stop(self, **kwargs):
        logger.info('%s: rotate_stop', self.dev.dev_id)
        indexes = []
        for value in [PTZ.LEFT_STOP, PTZ.RIGHT_STOP, PTZ.DOWN_STOP, PTZ.UP_STOP]:
            indexes.append(await self.send_command(JsonCommands.CMD_PTZ_CONTROL, parms=0, value=value.value))
            await asyncio.sleep(0.05)

        await asyncio.gather(*[self.wait_ack(idx) for idx in indexes])

    async def step_rotate(self, value):
        await self.rotate_start(value)
        # await asyncio.sleep(0.2)
        await self.rotate_stop()

    async def reboot(self, **kwargs):
        logger.info('%s: reboot', self.dev.dev_id)
        await self.control(reboot=1, no_ack=True)

    async def reset(self, **kwargs):
        """
        Reset to factory defaults
        """
        await self.control(reset=1)


class BinarySession(Session):
    DEFAULT_LOGIN = 'admin'
    DEFAULT_PASSWORD = '6666'
    ACKS = {
        BinaryCommands.CMD_SYSTEM_USER_CHK: BinaryCommands.ACK_SYSTEM_USER_CHK,
        BinaryCommands.CMD_PEER_VIDEOPARAM_SET: BinaryCommands.ACK_PEER_VIDEOPARAM_SET,
        BinaryCommands.CMD_PEER_LIVEVIDEO_START: BinaryCommands.ACK_PEER_LIVEVIDEO_START,
        BinaryCommands.CMD_PEER_LIVEVIDEO_STOP: BinaryCommands.ACK_PEER_LIVEVIDEO_STOP,
        BinaryCommands.CMD_SYSTEM_STATUS_GET: BinaryCommands.ACK_SYSTEM_STATUS_GET,
    }
    REV_ACKS = {v: k for k, v in ACKS.items()}

    def __init__(self, *args, login='', password='', **kwargs):
        super().__init__(*args, **kwargs)
        self.auth_login = login or self.DEFAULT_LOGIN
        self.auth_password = password or self.DEFAULT_PASSWORD
        self.ticket = b'\x00' * 4

    async def send_initial_packets(self):
        pkt = make_punch_pkt(self.dev.dev_id)
        await self.send(pkt)
        pkt.type = PacketType.P2pRdy
        await self.send(pkt)

    async def handle_drw(self, drw_pkt):
        await super().handle_drw(drw_pkt)
        self.last_drw_pkt_at = datetime.datetime.now()

        # # 0x10000 - max number of chunks in one epoch,we need to keep order of chunks
        pkt_epoch = self._get_drw_epoch(drw_pkt)

        if pkt_epoch > self.video_epoch:
            logger.info('Video epoch changed %s -> %s', self.video_epoch, pkt_epoch)
            self.video_epoch = pkt_epoch
            self.last_drw_pkt_idx = drw_pkt._cmd_idx
        elif self.last_drw_pkt_idx < drw_pkt._cmd_idx:
            self.last_drw_pkt_idx = drw_pkt._cmd_idx

        if drw_pkt._channel == Channel.Video:
            # logger.debug(f'Got video data {drw_pkt.get_drw_payload()}')
            if self.video_stale_at:
                logger.warning('Got video data while stale')
                self.video_stale_at = None
            self.video_chunk_queue.put_nowait((pkt_epoch, drw_pkt))
        elif drw_pkt._channel == Channel.Audio:
            pass
        elif drw_pkt._channel == Channel.Command:
            await self.handle_incoming_command_packet(drw_pkt)

    def _get_drw_epoch(self, drw_pkt):
        if self.last_drw_pkt_idx > 0xff00 and drw_pkt._cmd_idx < 0x100:
            return self.video_epoch + 1
        if self.video_epoch and self.last_drw_pkt_idx < 0x100 and drw_pkt._cmd_idx > 0xff00:
            return self.video_epoch - 1
        return self.video_epoch

    async def handle_incoming_command_packet(self, drw_pkt):
        if isinstance(drw_pkt, BinaryCmdPkt):
            if drw_pkt.command == BinaryCommands.ACK_SYSTEM_USER_CHK and len(drw_pkt.cmd_payload) >= 20:
                # this is from cam-reverse code
                self.ticket = drw_pkt.cmd_payload[16:20]
            logger.debug(
                'handle_incoming_command_packet: token=%s, %s data=%s',
                drw_pkt.token.hex(),
                drw_pkt.command,
                drw_pkt.cmd_payload.hex(' '),
            )

            if drw_pkt.command in self.REV_ACKS:
                waiter = self.cmd_waiters.pop(self.REV_ACKS[drw_pkt.command].value, None)
                # logger.info(f'{drw_pkt.command=} {self.REV_ACKS[drw_pkt.command]=} {waiter=} {drw_pkt.cmd_payload=}')
                if waiter:
                    waiter.set_result(drw_pkt.cmd_payload)

    async def send_command(self, cmd, cmd_payload=b'', *, with_response=False, **kwargs):
        pkt_idx = self.outgoing_command_idx
        self.outgoing_command_idx += 1
        pkt = BinaryCmdPkt(
            pkt_idx,
            cmd,
            cmd_payload,
            self.ticket,
        )
        if with_response:
            self.cmd_waiters[cmd.value] = asyncio.Future()
        await self.send(pkt)
        return pkt_idx

    async def wait_cmd_result(self, cmd, timeout=5):
        fut = self.cmd_waiters.get(cmd.value)
        if fut:
            res = await asyncio.wait_for(fut, timeout=timeout)
            logger.debug('Got command result %s', res)
            return res
        return b''

    @staticmethod
    def _get_video_params(mode):
        pairs = {
            # 320 x 240
            1: [
                [VideoParamType.VIDEO_PARAM_TYPE_RESOLUTION, VideoResolution.VIDEO_RESOLUTION_QVGA],
                # [VideoParamType.VIDEO_PARAM_TYPE_BITRATE, 0x20],
            ],
            # 640x480
            2: [
                [VideoParamType.VIDEO_PARAM_TYPE_RESOLUTION, VideoResolution.VIDEO_RESOLUTION_VGA],
                # [VideoParamType.VIDEO_PARAM_TYPE_BITRATE, 0x20],
            ],
            # 640x480
            3: [
                [VideoParamType.VIDEO_PARAM_TYPE_RESOLUTION, VideoResolution.VIDEO_RESOLUTION_HD],
                # [VideoParamType.VIDEO_PARAM_TYPE_BITRATE, 0x50],
            ],
            # also 640x480 on the X5 -- hwat now?
            4: [
                [VideoParamType.VIDEO_PARAM_TYPE_RESOLUTION, VideoResolution.VIDEO_RESOLUTION_FD],
                # [VideoParamType.VIDEO_PARAM_TYPE_BITRATE, 0x78],
            ],
            # also 640x480 on the X5 -- hwat now?
            5: [
                [VideoParamType.VIDEO_PARAM_TYPE_RESOLUTION, VideoResolution.VIDEO_RESOLUTION_UD],
                # [VideoParamType.VIDEO_PARAM_TYPE_BITRATE, 0xa0],
            ],
        }
        return [BinarySession._build_video_param(*x) for x in pairs[mode]]

    async def _request_video(self, mode):
        logger.info('Request video %s', mode)

        if mode == 1:
            video_params = self._get_video_params(3)
        elif mode == 2:
            video_params = self._get_video_params(1)
        else:
            video_params = []

        if mode:
            for video_param in video_params:
                await self.send_command(BinaryCommands.CMD_PEER_VIDEOPARAM_SET, video_param, with_response=True)
            await self.send_command(BinaryCommands.CMD_PEER_LIVEVIDEO_START, b'', with_response=True)
        else:
            await self.send_command(BinaryCommands.CMD_PEER_LIVEVIDEO_STOP, b'', with_response=True)

    @staticmethod
    def _build_video_param(param_type, value):
        if isinstance(param_type, VideoParamType):
            param = param_type
        else:
            param = VideoParamType[f'VIDEO_PARAM_TYPE_{param_type.upper()}'].value

        if isinstance(value, str):
            value = globals()[f'Video{param_type.capitalize()}'][f'VIDEO_{param_type.upper()}_{value.upper()}'].value

        return struct.pack('<II', param, value)

    async def set_video_param(self, name, value):
        payload = self._build_video_param(name, value)
        await self.send_command(BinaryCommands.CMD_PEER_VIDEOPARAM_SET, payload, with_response=True)

    async def login(self):
        # type is char account[0x20]; char password[0x80];
        payload = struct.pack('>32s128s', self.auth_login.encode('utf-8'), self.auth_password.encode('utf-8'))
        idx = await self.send_command(BinaryCommands.CMD_SYSTEM_USER_CHK, payload, with_response=True)
        await self.wait_ack(idx)
        auth_result = await self.wait_cmd_result(BinaryCommands.CMD_SYSTEM_USER_CHK)
        logger.debug(f"Connect user responded with {auth_result=}")
        if auth_result != b'':
            raise AuthError(f'Login failed: [{auth_result.hex(" ")}]')
        return True

    async def get_status(self):
        idx = await self.send_command(BinaryCommands.CMD_SYSTEM_STATUS_GET, b'', with_response=True)
        await self.wait_ack(idx)
        status_result = await self.wait_cmd_result(BinaryCommands.CMD_SYSTEM_STATUS_GET)
        return {**parse_dev_status(status_result), 'raw': status_result.hex(' ')}

    async def setup_device(self):
        await self.login()
        self.dev_properties = await self.get_status()
        logger.info('Camera properties: %s', self.dev_properties)
        self.device_is_ready.set()

    async def loop_step(self):
        await super().loop_step()

    async def reboot(self, **kwargs):
        await self.send_command(BinaryCommands.CMD_SYSTEM_REBOOT)

    async def reset(self, **kwargs):
        pass

    async def toggle_whitelight(self, value, **kwargs):
        await self.send_command(BinaryCommands.CMD_PEER_LIGHTFILL_ONOFF)

    async def toggle_ir(self, value, **kwargs):
        await self.send_command(BinaryCommands.CMD_PEER_IRCUT_ONOFF)

    async def toggle_lamp(self, value, **kwargs):
        pass

    async def rotate_start(self, value, **kwargs):
        ptz = PtzDirection[f'PTZ_DIRECTION_{value.upper()}'].value
        data = self._pack_ptz_dir_cmd(ptz)
        await self.send_command(BinaryCommands.CMD_PASSTHROUGH_STRING_PUT, data)

    async def rotate_stop(self, **kwargs):
        data = self._pack_ptz_dir_cmd(PtzDirection.PTZ_DIRECTION_STOP)
        await self.send_command(BinaryCommands.CMD_PASSTHROUGH_STRING_PUT, data)

    async def step_rotate(self, value, **kwargs):
        await self.rotate_start(value)
        await asyncio.sleep(0.2)
        await self.rotate_stop()

    @staticmethod
    def _pack_ptz_dir_cmd(ptz: PtzDirection) -> bytes:
        data = struct.pack('>III', PtzParamType.PTZ_PARAM_TYPE_DIRECTION, ptz, 0)
        return pack_passtrough_cmd(BinaryCommands.CMD_PTZ_SET.value, data)


class SharedFrameBuffer:
    def __init__(self):
        self.condition = asyncio.Condition()
        self.latest_frame = None

    async def publish(self, frame: VideoFrame):
        async with self.condition:
            self.latest_frame = frame
            self.condition.notify_all()

    async def get(self):
        async with self.condition:
            await self.condition.wait()
            return self.latest_frame


def make_session(device: DeviceDescriptor, on_device_lost: Callable[[DeviceDescriptor], None],
                 login: str = '', password: str = '') -> Session:
    """Create a session for the camera."""
    session_class = JsonSession if device.is_json else BinarySession
    return session_class(device, on_disconnect=on_device_lost, login=login, password=password)
