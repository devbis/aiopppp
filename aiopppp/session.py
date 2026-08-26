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
    CgiCommands,
    JsonCommands,
    LibError,
    PacketType,
    PtzDirection,
    PtzParamType,
    PtzPrefab,
    VideoParamType,
    VideoResolution,
    VideoRotate,
    enum_name,
)
from .audio import CODECS
from .encrypt import ENC_METHODS
from .exceptions import AuthError, CommandResultError
from .packets import (
    BinaryCmdPkt,
    JsonCmdPkt,
    make_audio_drw_pkt,
    make_close_pkt,
    make_drw_ack_pkt,
    make_p2palive_ack_pkt,
    make_p2palive_pkt,
    make_punch_pkt,
    pack_passtrough_cmd,
    parse_dev_status,
    parse_packet,
)
from .types import AudioFrame, Channel, DeviceDescriptor, VideoFrame
from .utils import DebounceEvent

logger = logging.getLogger(__name__)

# Prefix of the 0x20-byte header that marks the first chunk of a video frame.
VIDEO_MARKER = b'\x55\xaa\x15\xa8'
# Byte 4 of the 0x20 header is the stream type (captured from FTYC hardware;
# matches cam-reverse). FTYC muxes audio onto the VIDEO DRW channel: each video
# frame is preceded by one audio packet with the same magic but type 0x06.
STREAM_TYPE_JPEG = 0x03
STREAM_TYPE_AUDIO = 0x06


class SessionLogAdapter(logging.LoggerAdapter):
    """Tag every session log line with the device ID. Several cameras log
    through this module concurrently; untagged lines are unattributable."""

    def process(self, msg, kwargs):
        return f'[{self.extra["dev"]}] {msg}', kwargs


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
        # The frame currently being assembled is delimited by the top two
        # boundaries. We track that window and the set of still-missing chunk
        # indices in it incrementally, so completeness is an O(1) set update per
        # chunk instead of an O(frame) rescan (which was O(frame^2) per frame).
        self._frame_window = (None, None)
        self._frame_missing = set()
        # Chunks seen since the last frame-boundary header. If this grows large
        # the camera is streaming video we can't frame (e.g. a different marker
        # than VIDEO_MARKER) -- log a sample so the format can be identified.
        self._chunks_since_boundary = 0
        # Header diagnostics: some firmwares (FTYC) put the 0x20-byte stream
        # header on far more chunks than one per frame. Sample a few headers so
        # the type/length fields can be identified from a plain log.
        self._boundary_headers_logged = 0
        self._boundaries_seen = 0

    async def process_video_queue(self):
        while True:
            pkt_epoch, pkt = await self.video_chunk_queue.get()
            await self.handle_incoming_video_packet(pkt_epoch, pkt)

    def start_video_queue(self):
        self.process_video_task = asyncio.create_task(self.process_video_queue())

    async def handle_incoming_video_packet(self, pkt_epoch, pkt):
        video_payload = pkt.get_drw_payload()
        # self.log.info(f'- video frame {pkt._cmd_idx}')

        video_chunk_idx = pkt._cmd_idx + 0x10000 * pkt_epoch

        # 0x20 - size of the header starting with this magic
        if video_payload.startswith(VIDEO_MARKER):
            self._chunks_since_boundary = 0
            self._boundaries_seen += 1
            if self._boundary_headers_logged < 8 or self._boundaries_seen % 5000 == 0:
                self._boundary_headers_logged += 1
                self.log.info('stream header sample #%d (payload len=%d): [%s]',
                              self._boundaries_seen, len(video_payload),
                              video_payload[:0x20].hex(' '))
            stream_type = video_payload[4] if len(video_payload) > 4 else None
            if stream_type == STREAM_TYPE_AUDIO:
                # Muxed audio (FTYC): not a frame boundary. Occupy the index
                # with an empty chunk so the surrounding video frame's window
                # still completes, and hand the packet to the audio pipeline.
                self.video_received[video_chunk_idx] = b''
                await self.handle_incoming_audio_packet(pkt)
                await self.process_video_frame(video_chunk_idx)
                return
            self.video_boundaries.add(video_chunk_idx)
            self.video_received[video_chunk_idx] = video_payload[0x20:]
        else:
            self.video_received[video_chunk_idx] = video_payload
            self._chunks_since_boundary += 1
            if self._chunks_since_boundary in (100, 1000, 10000):
                self.log.warning(
                    'No frame boundary in %d video chunks; payload head: [%s]',
                    self._chunks_since_boundary, video_payload[:32].hex(' '),
                )
        await self.process_video_frame(video_chunk_idx)

    async def process_video_frame(self, new_idx=None):
        if len(self.video_boundaries) <= 1:
            return
        # After pruning, video_boundaries only holds the current pending pair
        # (plus any freshly-arrived higher boundary), so this sort is over a
        # handful of items.
        frame_starts = sorted(self.video_boundaries)
        index = frame_starts[-2]
        last_index = frame_starts[-1]

        if (index, last_index) != self._frame_window:
            # The frame window advanced. Recompute the missing set and drop
            # everything below the new frame start. Both are O(frame) but run
            # once per frame here, not once per incoming chunk.
            self._frame_window = (index, last_index)
            self._frame_missing = {i for i in range(index, last_index) if i not in self.video_received}
            for idx in [i for i in self.video_received if i < index]:
                del self.video_received[idx]
            for idx in [i for i in self.video_boundaries if i < index]:
                self.video_boundaries.discard(idx)
        elif new_idx is not None:
            # Same window: the chunk we just stored may have filled a gap.
            self._frame_missing.discard(new_idx)

        if index != self.last_video_frame and not self._frame_missing:
            self.last_video_frame = index
            data = b''.join(self.video_received[i] for i in range(index, last_index))
            # A reassembled MJPEG frame must be SOI..EOI; log rejects so a
            # polluted stream (e.g. muxed sub-streams) is visible in the log.
            if self.log.isEnabledFor(logging.DEBUG):
                valid = data[:2] == b'\xff\xd8'
                self.log.debug('publish frame idx=%s len=%d head=[%s]%s',
                               index, len(data), data[:4].hex(' '),
                               '' if valid else ' NOT-JPEG')
            if data:
                # FTYC frames a stream as [bare 0x20-header pkt][header+data
                # pkt][data...]: the two adjacent header chunks make a
                # zero-length "frame" between them. Publishing it emitted a
                # Content-Length: 0 MJPEG part after every real frame, which
                # froze browsers on the first image.
                await self.frame_buffer.publish(VideoFrame(idx=index, data=data))

        if self.log.isEnabledFor(logging.DEBUG):
            completeness = ''.join(
                'x' if i in self.video_received else '_'
                for i in range(index, last_index)
            )
            self.log.debug('.. completeness: %s', completeness)


class Session(PacketQueueMixin, VideoQueueMixin):
    # If no packet arrives from the camera for this many seconds, treat the
    # connection as dead and tear it down. Works for both JSON and binary
    # cameras (binary has no other liveness check), and catches a silently
    # dropped peer that would otherwise leave a zombie session.
    RECV_TIMEOUT_SEC = 20

    def __init__(self, dev, on_disconnect, *args, on_video_state_change=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.state = State.DISCONNECTED
        self.dev = dev
        self.log = SessionLogAdapter(logger, {'dev': dev.dev_id.dev_id})
        self.dev_properties = {}
        self.outgoing_command_idx = 0
        self.transport = None
        self.device_is_ready = asyncio.Event()
        self.is_video_requested = False
        self.video_stale_at = None
        self.last_alive_pkt_at = datetime.datetime.now()
        self.last_drw_pkt_at = datetime.datetime.now()
        self.last_recv_at = datetime.datetime.now()
        self.on_disconnect = on_disconnect
        # Called with the new is_video_requested value whenever streaming
        # starts or stops (including when the session is torn down).
        self.on_video_state_change = on_video_state_change
        self.main_task = None
        self.drw_waiters = {}
        self.cmd_waiters = {}
        self._p2p_rdy_debouncer = DebounceEvent(delay=0.2)

    def __str__(self):
        return f'Session({self.dev.dev_id}) ({self.state.name})'

    def _notify_video_state(self):
        if self.on_video_state_change:
            self.on_video_state_change(self.is_video_requested)

    async def create_udp(self):
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: SessionUDPProtocol(lambda data: self.on_receive(data)),
            remote_addr=(self.dev.addr, self.dev.port),
        )
        return transport

    def on_receive(self, data):
        # The transport is bound to the camera's address, so any datagram here
        # is proof of life for the dead-connection check in loop_step().
        self.last_recv_at = datetime.datetime.now()
        try:
            decoded = ENC_METHODS[self.dev.encryption][0](data)
            pkt = parse_packet(decoded)
        except (ValueError, struct.error, KeyError, IndexError):
            # One malformed datagram must never raise out of the asyncio
            # datagram callback (which would spam "Exception in callback" and,
            # in the worst case, wedge the transport). Log and drop it.
            self.log.debug('Dropping undecodable datagram (%d bytes): [%s]', len(data), data[:16].hex(' '))
            return
        # self.log.debug(f"recv< {pkt} {pkt.get_payload()}")
        self.log.debug(f"recv< {pkt.type}, len={len(pkt.get_payload())}")
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

    # Cap on outstanding DRW ACK waiters. A waiter is created for every DRW we
    # send but only removed when its ACK arrives (handle_drw_ack) or its wait
    # times out (_wait_ack). Fire-and-forget commands (reboot, toggle_*, PTZ)
    # never wait, so their waiters would linger; bound the dict and evict the
    # oldest so it can never grow without limit.
    MAX_DRW_WAITERS = 256

    async def _send(self, pkt):
        self.log.debug(f"send> {pkt}")
        if pkt.type == PacketType.Drw:
            existing = self.drw_waiters.get(pkt._cmd_idx)
            if existing is not None and not existing.done():
                # The 16-bit index wrapped back onto a still-pending waiter; that
                # old send will never be matched now, so discard it.
                existing.cancel()
            self.drw_waiters[pkt._cmd_idx] = asyncio.Future()
            while len(self.drw_waiters) > self.MAX_DRW_WAITERS:
                old_idx, old_fut = next(iter(self.drw_waiters.items()))
                del self.drw_waiters[old_idx]
                if not old_fut.done():
                    old_fut.cancel()

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
            self.log.debug(f'Got DRW ACK {pkt}')
            await self.handle_drw_ack(pkt)
        elif pkt.type == PacketType.P2PAliveAck:
            self.log.debug(f'Got P2PAlive ACK {pkt}')
        elif pkt.type == PacketType.Close:
            await self.handle_close(pkt)
        else:
            self.log.warning(f'Got UNKNOWN {pkt}')

    async def login(self):
        pass

    async def start_video(self):
        await self.device_is_ready.wait()
        if not self.is_video_requested:
            self.log.info('Start video')
            self.last_drw_pkt_at = datetime.datetime.now()
            await self._request_video(1)
            self.is_video_requested = True
            self._notify_video_state()

    async def stop_video(self):
        if self.is_video_requested:
            self.is_video_requested = False
            self._notify_video_state()
            self.video_stale_at = None
            self.video_received = {}
            self.video_boundaries = set()
            self.video_epoch = 0
            self.last_video_frame = -1
            self._frame_window = (None, None)
            self._frame_missing = set()
            while not self.video_chunk_queue.empty():
                self.video_chunk_queue.get_nowait()
            await self._request_video(0)

    async def _request_video(self, mode):
        """
        Mode is 1 for 640x480 or 2 for 320x240
        """
        pass

    async def handle_drw(self, drw_pkt):
        self.log.debug('handle_drw(idx=%s, chn=%s)', drw_pkt._cmd_idx, drw_pkt._channel)
        await self.send(make_drw_ack_pkt(drw_pkt))
        self.last_drw_pkt_at = datetime.datetime.now()

        if drw_pkt._channel == Channel.Video:
            # The camera counts the DRW index independently per channel, so only
            # video-channel packets may drive epoch/wraparound tracking. Feeding
            # command/audio indices (which advance on their own) in here would
            # spuriously flip video_epoch and corrupt frame reassembly by an
            # 0x10000 index shift.
            pkt_epoch = self._get_drw_epoch(drw_pkt)
            if pkt_epoch > self.video_epoch:
                self.log.info('Video epoch changed %s -> %s', self.video_epoch, pkt_epoch)
                self.video_epoch = pkt_epoch
                self.last_drw_pkt_idx = drw_pkt._cmd_idx
            elif pkt_epoch == self.video_epoch and self.last_drw_pkt_idx < drw_pkt._cmd_idx:
                # Only chunks from the CURRENT epoch may advance the high-water
                # mark. A late pre-wrap chunk (e.g. idx 65529 retransmitted
                # after the counter wrapped to 0) belongs to the previous epoch;
                # feeding it in here re-armed the wrap detector and the next
                # post-wrap chunk bumped the epoch again -- ping-ponging the
                # epoch up several times a second (seen on FTYC hardware,
                # epochs 0->6 in seconds) and scattering reassembly indices
                # 0x10000 apart, which killed every frame after the first.
                self.last_drw_pkt_idx = drw_pkt._cmd_idx

            if self.video_stale_at:
                self.log.warning('Got video data while stale')
                self.video_stale_at = None
            self.video_chunk_queue.put_nowait((pkt_epoch, drw_pkt))
        elif drw_pkt._channel == Channel.Audio:
            await self.handle_incoming_audio_packet(drw_pkt)
        elif drw_pkt._channel == Channel.Command:
            await self.handle_incoming_command_packet(drw_pkt)

    def _get_drw_epoch(self, drw_pkt):
        if self.last_drw_pkt_idx > 0xff00 and drw_pkt._cmd_idx < 0x100:
            return self.video_epoch + 1
        if self.video_epoch and self.last_drw_pkt_idx < 0x100 and drw_pkt._cmd_idx > 0xff00:
            return self.video_epoch - 1
        return self.video_epoch

    async def handle_incoming_command_packet(self, drw_pkt):
        pass

    async def handle_incoming_audio_packet(self, drw_pkt):
        pass

    def _reset_cmd_waiter(self, cmd):
        # Replace any pending response future for this command. Without this a
        # second request whose first response never arrived would silently
        # orphan the old future (and its awaiter would hang until timeout).
        old = self.cmd_waiters.get(cmd.value)
        if old is not None and not old.done():
            old.cancel()
        fut = asyncio.Future()
        self.cmd_waiters[cmd.value] = fut
        return fut

    async def handle_drw_ack(self, pkt):
        cmd_idx_ack = int.from_bytes(pkt.get_payload()[4:6], 'big')
        self.log.debug('handle_drw_ack(idx=%s)', cmd_idx_ack)
        fut = self.drw_waiters.pop(cmd_idx_ack, None)
        if fut is not None and not fut.done():
            fut.set_result(pkt)

    async def wait_ack(self, idx, timeout=5):
        return await self.call_with_error_check(self._wait_ack(idx, timeout))

    async def _wait_ack(self, idx, timeout=5):
        if idx is None:
            raise ValueError('Need to provide numeric command index')
        fut = self.drw_waiters.get(idx)
        if fut:
            self.log.debug(f'Waiting for ACK for {idx}')
            try:
                await asyncio.wait_for(fut, timeout=timeout)
                self.log.debug('wait_ack(idx=%d) complete, waiters: %d', idx, len(self.drw_waiters))
            except asyncio.TimeoutError:
                self.drw_waiters.pop(idx, None)
                raise

    async def handle_close(self, pkt):
        self.log.info('peer requested close')
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
            try:
                await asyncio.wait_for(self._p2p_rdy_debouncer.wait(), timeout=10)
            except asyncio.TimeoutError:
                # Camera answered discovery but never completed the P2pRdy
                # handshake (common when it is flaky/half-wedged). Treat it as a
                # lost device rather than letting an unhandled exception escape
                # and take the whole process down.
                self.log.warning('did not become ready (no P2pRdy), disconnecting')
                await self.send_close_pkt()
                self._on_device_lost()
                return
            self.log.info('Connected at %s, json=%s', self.dev.addr, self.dev.is_json)
            self.state = State.CONNECTED
            try:
                await self.setup_device()
            except asyncio.TimeoutError:
                self.log.error('Timeout during device setup')
                await self.send_close_pkt()
                self._on_device_lost()
                return

            while True:
                await self.loop_step()
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            if self.transport:
                self.log.debug('Session main task cancelled, sending close packet')
                await self.send_close_pkt()
            raise
        except Exception:
            # A single session must never crash the whole process. Log it, tear
            # the session down, and let discovery/HA reconnect.
            self.log.exception('Session failed; disconnecting')
            try:
                await self.send_close_pkt()
            except Exception:
                pass
            self._on_device_lost()
            return

    # Seconds of no video (while streaming) before we re-request it, and the
    # further grace period before giving up on the connection.
    VIDEO_REREQUEST_SEC = 5
    VIDEO_DEAD_SEC = 10

    async def loop_step(self):
        self.log.debug("iterate in Session")
        now = datetime.datetime.now()

        # Video liveness. Applies to both protocols: a binary camera that keeps
        # answering P2PAlive but sends no video would otherwise pass the base
        # receive-timeout check forever and zombie. Re-request after a short
        # gap, then disconnect if that doesn't revive the stream.
        if (
            self.is_video_requested and not self.video_stale_at and
            (now - self.last_drw_pkt_at).total_seconds() > self.VIDEO_REREQUEST_SEC
        ):
            self.video_stale_at = self.last_drw_pkt_at
            self.log.info('No video for %ds. Re-requesting video', self.VIDEO_REREQUEST_SEC)
            await self._request_video(1)
        if self.video_stale_at and (now - self.video_stale_at).total_seconds() > self.VIDEO_DEAD_SEC:
            self.log.warning('No video for %ds. Disconnecting', self.VIDEO_DEAD_SEC)
            await self.send_close_pkt()
            self._on_device_lost()
            return

        if (now - self.last_recv_at).total_seconds() > self.RECV_TIMEOUT_SEC:
            self.log.warning(
                'No packets from %s for %ds: connection is dead, disconnecting',
                self.dev.dev_id, self.RECV_TIMEOUT_SEC,
            )
            await self.send_close_pkt()
            self._on_device_lost()
            return
        if (now - self.last_alive_pkt_at).total_seconds() > 10:
            self.last_alive_pkt_at = now
            self.log.info('Send P2PAlive')
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
        self.log.warning('Device lost')
        self.stop()
        if self.on_disconnect:
            self.on_disconnect(self.dev)

    def stop(self):
        if self.state == State.DISCONNECTED and self.transport is None:
            # Already fully stopped. stop() is reachable from _on_device_lost(),
            # Device.close() and the CLI shutdown loop, so it must be idempotent.
            # Note: a session that started connecting but never reached CONNECTED
            # (e.g. P2pRdy timeout) is still DISCONNECTED but has a live transport
            # and queue tasks, so we must fall through and clean those up.
            return
        self.log.info('Stopping session tasks')
        self.device_is_ready.set()
        reassert_task = getattr(self, '_reassert_task', None)
        if reassert_task and not reassert_task.done():
            reassert_task.cancel()
        if self.process_packet_task:
            self.process_packet_task.cancel()
        if self.process_video_task:
            self.process_video_task.cancel()
        if self.main_task:
            self.main_task.cancel()
        if self.transport:
            self.transport.close()
            self.transport = None
        if self.is_video_requested:
            # The session is going away, so streaming has effectively stopped.
            self.is_video_requested = False
            self._notify_video_state()
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
        # The index is sent as a 16-bit field and ACKs only echo 16 bits, so it
        # must wrap; otherwise sends raise struct.error and ACK matching breaks.
        self.outgoing_command_idx = (self.outgoing_command_idx + 1) & 0xFFFF
        pkt = JsonCmdPkt(pkt_idx, {**data, **kwargs, **self.get_common_data()})
        if with_response:
            self._reset_cmd_waiter(cmd)
        await self.send(pkt)
        return pkt_idx

    async def login(self):
        idx = await self.send_command(JsonCommands.CMD_CHECK_USER, with_response=True)
        await self.wait_ack(idx)
        auth_result = await self.wait_cmd_result(JsonCommands.CMD_CHECK_USER)
        if auth_result['result'] != 0:
            raise AuthError(f'Login failed: {auth_result}')
        return True

    async def _request_video(self, mode):
        self.log.info('Request video %s', mode)
        await self.send_command(JsonCommands.CMD_STREAM, video=mode)

    async def handle_incoming_command_packet(self, drw_pkt):
        if isinstance(drw_pkt, JsonCmdPkt):
            response = drw_pkt.json_payload
            fut = self.cmd_waiters.get(response['cmd'])
            # Resolve but don't remove the waiter here: wait_cmd_result looks it
            # up by key *after* wait_ack, so popping now would lose a result that
            # arrives before the caller starts awaiting it.
            if fut is not None and not fut.done():
                fut.set_result(response)

    async def wait_cmd_result(self, cmd, timeout=5):
        return await self.call_with_error_check(self._wait_cmd_result(cmd, timeout))

    async def _wait_cmd_result(self, cmd, timeout=5):
        fut = self.cmd_waiters.get(cmd.value)
        if fut:
            try:
                res = await asyncio.wait_for(fut, timeout=timeout)
            finally:
                self.cmd_waiters.pop(cmd.value, None)
            self.log.debug('Got command result %s', res)
            return res
        return {'result': -1}

    async def setup_device(self):
        auth = await self.login()
        idx = await self.send_command(JsonCommands.CMD_GET_PARMS, with_response=True)
        # self.log.debug('Waiting for params ack')
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
        self.dev_properties['auth'] = auth
        self.log.info('Camera properties: %s', cam_properties)
        self.device_is_ready.set()

    async def control(self, no_ack=False, **kwargs):
        idx = await self.send_command(JsonCommands.CMD_DEV_CONTROL, **kwargs)
        if not no_ack:
            await self.wait_ack(idx)

    async def toggle_lamp(self, value):
        await self.control(lamp=1 if value else 0)

    async def toggle_whitelight(self, value, **kwargs):
        self.log.info('toggle white light = %s', value)
        idx = await self.send_command(JsonCommands.CMD_SET_WHITELIGHT, status=value)
        await self.wait_ack(idx)

    async def toggle_ir(self, value):
        self.log.info('toggle IR = %s', value)
        # control() already waits for the ACK; it returns None, so the previous
        # `await self.wait_ack(idx)` raised ValueError on every call.
        await self.control(icut=1 if value else 0)

    async def rotate_start(self, value):
        self.log.info('rotate_start %s', value)
        value = PTZ[f'{value.upper()}_START'].value
        idx = await self.send_command(JsonCommands.CMD_PTZ_CONTROL, parms=0, value=value)
        await self.wait_ack(idx)

    async def rotate_stop(self, **kwargs):
        self.log.info('rotate_stop')
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
        self.log.info('reboot')
        await self.control(reboot=1, no_ack=True)

    async def reset(self, **kwargs):
        """
        Reset to factory defaults
        """
        await self.control(reset=1)


class BinarySession(Session):
    DEFAULT_LOGIN = 'admin'
    DEFAULT_PASSWORD = 'admin' #'6666'
    ACKS = {
        BinaryCommands.CMD_SYSTEM_USER_CHK: BinaryCommands.ACK_SYSTEM_USER_CHK,
        BinaryCommands.CMD_PEER_VIDEOPARAM_SET: BinaryCommands.ACK_PEER_VIDEOPARAM_SET,
        BinaryCommands.CMD_PEER_LIVEVIDEO_START: BinaryCommands.ACK_PEER_LIVEVIDEO_START,
        BinaryCommands.CMD_PEER_LIVEVIDEO_STOP: BinaryCommands.ACK_PEER_LIVEVIDEO_STOP,
        BinaryCommands.CMD_SYSTEM_STATUS_GET: BinaryCommands.ACK_SYSTEM_STATUS_GET,
        BinaryCommands.CMD_PEER_IRCUT_ONOFF: BinaryCommands.ACK_PEER_IRCUT_ONOFF,
        BinaryCommands.CMD_PEER_LIGHTFILL_ONOFF: BinaryCommands.ACK_PEER_LIGHTFILL_ONOFF,
        BinaryCommands.CMD_SYSTEM_REBOOT: BinaryCommands.ACK_SYSTEM_REBOOT,
        BinaryCommands.CMD_SNAPSHOT_GET: BinaryCommands.ACK_SNAPSHOT_GET,
        BinaryCommands.CMD_PEER_VIDEOPARAM_GET: BinaryCommands.ACK_PEER_VIDEOPARAM_GET,
        BinaryCommands.CMD_SYSTEM_INF_GET: BinaryCommands.ACK_SYSTEM_INF_GET,
        BinaryCommands.CMD_SYSTEM_ALIAS_SET: BinaryCommands.ACK_SYSTEM_ALIAS_SET,
        BinaryCommands.CMD_SYSTEM_DATETIME_GET: BinaryCommands.ACK_SYSTEM_DATETIME_GET,
        BinaryCommands.CMD_SYSTEM_DATETIME_SET: BinaryCommands.ACK_SYSTEM_DATETIME_SET,
        BinaryCommands.CMD_SYSTEM_USER_GET: BinaryCommands.ACK_SYSTEM_USER_GET,
        BinaryCommands.CMD_NET_WIFISETTING_GET: BinaryCommands.ACK_NET_WIFISETTING_GET,
        BinaryCommands.CMD_NET_WIFISETTING_SET: BinaryCommands.ACK_NET_WIFISETTING_SET,
        BinaryCommands.CMD_NET_WIFI_SCAN: BinaryCommands.ACK_NET_WIFI_SCAN,
        BinaryCommands.CMD_NET_WIREDSETTING_GET: BinaryCommands.ACK_NET_WIREDSETTING_GET,
        BinaryCommands.CMD_SD_INFO_GET: BinaryCommands.ACK_SD_INFO_GET,
        BinaryCommands.CMD_SD_RECORDFILE_GET: BinaryCommands.ACK_SD_RECORDFILE_GET,
        BinaryCommands.CMD_SD_PICFILE_GET: BinaryCommands.ACK_SD_PICFILE_GET,
        BinaryCommands.CMD_SD_RECORDING_NOW: BinaryCommands.ACK_SD_RECORDING_NOW,
        BinaryCommands.CMD_PEER_PLAYBACK_START: BinaryCommands.ACK_PEER_PLAYBACK_START,
        BinaryCommands.CMD_PEER_PLAYBACK_STOP: BinaryCommands.ACK_PEER_PLAYBACK_STOP,
        BinaryCommands.CMD_PEER_PLAYBACK_SEEK: BinaryCommands.ACK_PEER_PLAYBACK_SEEK,
        BinaryCommands.CMD_PEER_PLAYBACK_SPEED: BinaryCommands.ACK_PEER_PLAYBACK_SPEED,
        BinaryCommands.CMD_PEER_PLAYBACK_PAUSE: BinaryCommands.ACK_PEER_PLAYBACK_PAUSE,
        BinaryCommands.CMD_PEER_PLAYBACK_RESUME: BinaryCommands.ACK_PEER_PLAYBACK_RESUME,
        BinaryCommands.CMD_PEER_LIVEAUDIO_START: BinaryCommands.ACK_PEER_LIVEAUDIO_START,
        BinaryCommands.CMD_PEER_LIVEAUDIO_STOP: BinaryCommands.ACK_PEER_LIVEAUDIO_STOP,
        BinaryCommands.CMD_PEER_AUDIOPARAM_GET: BinaryCommands.ACK_PEER_AUDIOPARAM_GET,
    }
    REV_ACKS = {v: k for k, v in ACKS.items()}

    def __init__(self, *args, login='', password='', audio_codec='alaw', **kwargs):
        super().__init__(*args, **kwargs)
        self.auth_login = login or self.DEFAULT_LOGIN
        self.auth_password = password or self.DEFAULT_PASSWORD
        self.ticket = b'\x00' * 4
        # Last result code seen per command; a refusal has no payload, so this
        # is what tells "refused" apart from "answered with nothing".
        self.cmd_results = {}
        self._reassert_task = None
        # Received-audio pipeline (G.711 -> PCM), talk-back state.
        self.audio_buffer = SharedFrameBuffer()
        self.audio_codec = audio_codec if audio_codec in CODECS else 'alaw'
        self.is_audio_requested = False
        self._outgoing_audio_idx = 0

    async def send_initial_packets(self):
        pkt = make_punch_pkt(self.dev.dev_id)
        await self.send(pkt)
        pkt.type = PacketType.P2pRdy
        await self.send(pkt)

    @staticmethod
    def _result_code(drw_pkt):
        """Decode a reply's 4-byte token as a LibError. A refused command
        answers with no payload and a negative code here. Returns None when the
        token isn't a known code -- on success it carries the login ticket."""
        code = struct.unpack('<i', drw_pkt.token)[0]
        try:
            return LibError(code)
        except ValueError:
            return None

    async def handle_incoming_command_packet(self, drw_pkt):
        if isinstance(drw_pkt, BinaryCmdPkt):
            if drw_pkt.command == BinaryCommands.ACK_SYSTEM_USER_CHK and len(drw_pkt.cmd_payload) > 0:
                # this is from cam-reverse code
                self.ticket = drw_pkt.cmd_payload[4:8]
            result = self._result_code(drw_pkt)
            if drw_pkt.command in self.REV_ACKS:
                self.cmd_results[self.REV_ACKS[drw_pkt.command].value] = result
            if result is not None and result < LibError.OK:
                self.log.warning(
                    '%s refused by camera: %s (%d)', drw_pkt.command.name, result.name, result.value,
                )
            self.log.debug(
                'handle_incoming_command_packet: token=%s, ticket=%s, %s data=%s (%s)',
                drw_pkt.token.hex(),
                self.ticket.hex(),
                drw_pkt.command,
                drw_pkt.cmd_payload.hex(' '),
                len(drw_pkt.cmd_payload)
            )

            if drw_pkt.command in self.REV_ACKS:
                # Resolve but keep the waiter; wait_cmd_result pops it after
                # awaiting. Popping here races an ACK that arrives during the
                # preceding wait_ack, which would drop the result (e.g. a camera
                # that answers a snapshot/status request instantly).
                key = self.REV_ACKS[drw_pkt.command].value
                fut = self.cmd_waiters.get(key)
                if fut is not None and not fut.done():
                    fut.set_result(drw_pkt.cmd_payload)

    async def send_command(self, cmd, cmd_payload=b'', *, with_response=False, **kwargs):
        pkt_idx = self.outgoing_command_idx
        # The index is sent as a 16-bit field and ACKs only echo 16 bits, so it
        # must wrap; otherwise sends raise struct.error and ACK matching breaks.
        self.outgoing_command_idx = (self.outgoing_command_idx + 1) & 0xFFFF
        pkt = BinaryCmdPkt(
            pkt_idx,
            cmd,
            cmd_payload,
            self.ticket,
        )
        if with_response:
            self._reset_cmd_waiter(cmd)
        await self.send(pkt)
        return pkt_idx

    async def wait_cmd_result(self, cmd, timeout=5):
        fut = self.cmd_waiters.get(cmd.value)
        if fut:
            try:
                res = await asyncio.wait_for(fut, timeout=timeout)
            finally:
                self.cmd_waiters.pop(cmd.value, None)
            self.log.debug('Got command result %s', res)
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
        self.log.info('Request video %s', mode)

        if mode == 1:
            video_params = self._get_video_params(3)
        elif mode == 2:
            video_params = self._get_video_params(1)
        else:
            video_params = []

        if mode:
            for video_param in video_params:
                await self.send_command(BinaryCommands.CMD_PEER_VIDEOPARAM_SET, video_param)
            await self.send_command(BinaryCommands.CMD_PEER_LIVEVIDEO_START, b'')
            # The camera adaptively drops the resolution a few seconds after the
            # stream starts and ignores the resolution we set at start time.
            # Re-asserting it mid-stream (which is what re-selecting it in the UI
            # does) makes it stick, so schedule a delayed re-send. Keep a handle
            # so it can't be garbage-collected mid-flight and is cancelled on stop.
            if self._reassert_task and not self._reassert_task.done():
                self._reassert_task.cancel()
            self._reassert_task = asyncio.create_task(self._reassert_video_params(video_params))
        else:
            await self.send_command(BinaryCommands.CMD_PEER_LIVEVIDEO_STOP, b'')

    async def _reassert_video_params(self, video_params, delay=5):
        """Re-send the resolution a few seconds in to lock it (camera ignores
        the value set at stream start and self-downgrades otherwise)."""
        try:
            await asyncio.sleep(delay)
            if not self.is_video_requested or self.transport is None:
                return
            self.log.info('re-asserting video params to lock resolution')
            for video_param in video_params:
                await self.send_command(BinaryCommands.CMD_PEER_VIDEOPARAM_SET, video_param)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.log.debug('Re-assert video params failed', exc_info=True)

    @staticmethod
    def _build_video_param(param_type, value):
        if isinstance(param_type, VideoParamType):
            param = param_type.value
            name = param_type.name.replace('VIDEO_PARAM_TYPE_', '')
        else:
            name = str(param_type).upper()
            param = VideoParamType[f'VIDEO_PARAM_TYPE_{name}'].value

        if isinstance(value, Enum):
            value = value.value
        elif isinstance(value, str):
            # Resolve a symbolic value (e.g. 'HD') against the matching
            # Video<Name> enum, e.g. VideoResolution.VIDEO_RESOLUTION_HD.
            enum_cls = globals()[f'Video{name.capitalize()}']
            value = enum_cls[f'VIDEO_{name}_{value.upper()}'].value

        return struct.pack('<II', param, value)

    async def set_video_param(self, name, value):
        payload = self._build_video_param(name, value)
        await self.send_command(BinaryCommands.CMD_PEER_VIDEOPARAM_SET, payload)

    async def get_video_param(self, param, timeout=5):
        """Read back a single video parameter. `param` may be a VideoParamType
        or a symbolic name (e.g. 'resolution'). Returns the raw ACK payload."""
        if isinstance(param, VideoParamType):
            param_val = param.value
        else:
            param_val = VideoParamType[f'VIDEO_PARAM_TYPE_{str(param).upper()}'].value
        idx = await self.send_command(
            BinaryCommands.CMD_PEER_VIDEOPARAM_GET, struct.pack('<I', param_val), with_response=True,
        )
        await self.wait_ack(idx)
        return await self.wait_cmd_result(BinaryCommands.CMD_PEER_VIDEOPARAM_GET, timeout=timeout)

    @staticmethod
    def decode_video_param(payload, param):
        """Pull one parameter's value out of a VIDEOPARAM_GET response.

        Cameras answer in one of three shapes: the full table of params 1..12
        (u32 each -- PTZA/FTYC ignore the requested id and send everything),
        a (param, value) pair, or a bare value. Returns None if the payload
        doesn't carry the requested parameter."""
        if isinstance(param, VideoParamType):
            param_id = param.value
        else:
            param_id = VideoParamType[f'VIDEO_PARAM_TYPE_{str(param).upper()}'].value
        if len(payload) >= 48:
            table = struct.unpack_from('<12I', payload)
            return table[param_id - 1] if 1 <= param_id <= 12 else None
        if len(payload) >= 8:
            got_param, value = struct.unpack_from('<II', payload)
            return value if got_param == param_id else None
        if len(payload) >= 4:
            return struct.unpack_from('<I', payload)[0]
        return None

    async def get_video_param_value(self, param, timeout=5):
        """Read one video parameter and return it decoded (None if absent)."""
        payload = await self.get_video_param(param, timeout=timeout)
        return self.decode_video_param(payload, param)

    # Convenience setters -- thin wrappers over set_video_param so callers don't
    # have to know the symbolic parameter names. Values may be ints or symbolic
    # strings (e.g. resolution 'hd', rotate 'h').
    async def set_resolution(self, value):
        await self.set_video_param('resolution', value)

    async def set_bitrate(self, value):
        await self.set_video_param('bitrate', value)

    async def set_framerate(self, value):
        await self.set_video_param('framerate', value)

    async def set_brightness(self, value):
        await self.set_video_param('brightness', value)

    async def set_contrast(self, value):
        await self.set_video_param('contrast', value)

    async def set_saturation(self, value):
        await self.set_video_param('saturation', value)

    async def set_sharpness(self, value):
        await self.set_video_param('sharpness', value)

    async def set_rotate(self, value):
        """Flip/mirror the image. Value: VideoRotate or 'normal'/'h'/'v'/'hv'."""
        await self.set_video_param('rotate', value)

    async def set_scene(self, value):
        """Scene mode: 'scene_normal'/'scene_human'/'scene_cool'/'scene_blacklight'
        or the corresponding VideoParamType (they double as the mode selector)."""
        if isinstance(value, str) and not value.upper().startswith('SCENE_'):
            value = f'scene_{value}'
        param = VideoParamType[f'VIDEO_PARAM_TYPE_{str(value).upper()}']
        # Scene modes are selected by their own param type with value 1.
        await self.send_command(
            BinaryCommands.CMD_PEER_VIDEOPARAM_SET, struct.pack('<II', param.value, 1),
        )

    async def login(self):
        # type is char account[0x20]; char password[0x80];
        payload = struct.pack('>32s128s', self.auth_login.encode('utf-8'), self.auth_password.encode('utf-8'))
        idx = await self.send_command(BinaryCommands.CMD_SYSTEM_USER_CHK, payload, with_response=True)
        await self.wait_ack(idx)
        auth_result = await self.wait_cmd_result(BinaryCommands.CMD_SYSTEM_USER_CHK)
        self.log.debug(f"Connect user responded with {auth_result=}")
        if auth_result == b'':
            # Not fatal: only reboot, wifi scan and the user list need a login.
            # Everything else -- video, audio, PTZ, lights, status, video params,
            # time sync -- works unauthenticated. See VENDOR_APP_FINDINGS.md #12.
            reason = self.cmd_results.get(BinaryCommands.CMD_SYSTEM_USER_CHK.value)
            self.log.error(
                'Login failed for user %r: %s', self.auth_login,
                f'{reason.name} ({reason.value})' if reason is not None else 'no reason given',
            )
            return False
        return True

    async def get_status(self):
        idx = await self.send_command(BinaryCommands.CMD_SYSTEM_STATUS_GET, b'', with_response=True)
        await self.wait_ack(idx)
        status_result = await self.wait_cmd_result(BinaryCommands.CMD_SYSTEM_STATUS_GET)
        return {**parse_dev_status(status_result), 'raw': status_result.hex(' ')}

    async def _request(self, cmd, payload=b'', timeout=5):
        """Send a command that expects a response and return its raw ACK
        payload (b'' on timeout/no-answer)."""
        idx = await self.send_command(cmd, payload, with_response=True)
        await self.wait_ack(idx)
        return await self.wait_cmd_result(cmd, timeout=timeout)

    async def get_device_info(self, timeout=5):
        """Fetch the extended device-info block (CMD_SYSTEM_INF_GET). Returned
        as raw bytes -- the struct layout is firmware-specific and unverified."""
        return await self._request(BinaryCommands.CMD_SYSTEM_INF_GET, timeout=timeout)

    async def set_alias(self, name):
        """Set the camera's display name/alias."""
        payload = struct.pack('<64s', name.encode('utf-8')[:64])
        await self.send_command(BinaryCommands.CMD_SYSTEM_ALIAS_SET, payload)

    async def get_datetime(self, timeout=5):
        return await self._request(BinaryCommands.CMD_SYSTEM_DATETIME_GET, timeout=timeout)

    async def set_datetime(self, when=None, tz_seconds=None, ntp_server=None):
        """Set the device clock. Sends the full 80-byte datetime struct
        mirroring DATETIME_GET (confirmed on PTZA hardware): u32 unix
        timestamp (UTC epoch), i32 timezone as seconds WEST of UTC (UTC+2 is
        stored as -7200), 8 pad bytes, char ntp_server[64]. Some firmwares
        (FTYC) apply the timestamp but not the timezone when sent only the
        8-byte prefix, hence the full struct. With tz_seconds=None the host's
        current UTC offset is used; with ntp_server=None the camera's current
        NTP server is preserved (read back via DATETIME_GET, defaulting to
        time.windows.com if it doesn't answer)."""
        if when is None:
            when = datetime.datetime.now()
        if tz_seconds is None:
            offset = datetime.datetime.now().astimezone().utcoffset()
            tz_seconds = -int(offset.total_seconds()) if offset else 0
        if ntp_server is None:
            ntp_server = 'time.windows.com'
            try:
                current = await self.get_datetime(timeout=2)
                if len(current) >= 80:
                    ntp_server = current[16:80].split(b'\x00', 1)[0].decode('ascii') or ntp_server
            except Exception:
                self.log.debug('DATETIME_GET before set failed; using default NTP server')
        ts = int(when.timestamp())
        payload = struct.pack('<Ii8x64s', ts, int(tz_seconds), ntp_server.encode('ascii')[:64])
        await self.send_command(BinaryCommands.CMD_SYSTEM_DATETIME_SET, payload)

    async def get_users(self, timeout=5):
        return await self._request(BinaryCommands.CMD_SYSTEM_USER_GET, timeout=timeout)

    async def get_wifi_settings(self, timeout=5):
        return await self._request(BinaryCommands.CMD_NET_WIFISETTING_GET, timeout=timeout)

    async def set_wifi(self, ssid, password, enabled=True):
        """Provision the camera's Wi-Fi station credentials. Wire layout is a
        best-effort ssid[32]/password[64]/enabled block and unverified."""
        payload = struct.pack('<32s64sI', ssid.encode('utf-8')[:32],
                              password.encode('utf-8')[:64], 1 if enabled else 0)
        await self.send_command(BinaryCommands.CMD_NET_WIFISETTING_SET, payload)

    async def scan_wifi(self, timeout=10):
        """Trigger a Wi-Fi scan and return the raw result list."""
        return await self._request(BinaryCommands.CMD_NET_WIFI_SCAN, timeout=timeout)

    async def get_wired_settings(self, timeout=5):
        return await self._request(BinaryCommands.CMD_NET_WIREDSETTING_GET, timeout=timeout)

    # --- SD card & playback ------------------------------------------------
    # Reference: intx82/a9-v720. Payloads (date filters, filenames, offsets)
    # follow the decompiled command table / PlaybackCtrlBean and are all
    # unverified against hardware; GET calls return raw ACK bytes for the
    # caller to parse per its device.

    async def get_sd_info(self, timeout=5):
        """SD card capacity/status block (CMD_SD_INFO_GET)."""
        return await self._request(BinaryCommands.CMD_SD_INFO_GET, timeout=timeout)

    async def list_recordings(self, day=None, timeout=10):
        """List recorded video files, optionally filtered to a given date.
        `day` may be a datetime/date; sent as an 8-byte YYYYMMDD ascii filter."""
        payload = b''
        if day is not None:
            payload = day.strftime('%Y%m%d').encode('ascii')
        return await self._request(BinaryCommands.CMD_SD_RECORDFILE_GET, payload, timeout=timeout)

    async def list_pictures(self, day=None, timeout=10):
        payload = b''
        if day is not None:
            payload = day.strftime('%Y%m%d').encode('ascii')
        return await self._request(BinaryCommands.CMD_SD_PICFILE_GET, payload, timeout=timeout)

    async def is_recording(self, timeout=5):
        return await self._request(BinaryCommands.CMD_SD_RECORDING_NOW, timeout=timeout)

    @staticmethod
    def _playback_payload(filename='', offset=0):
        name = filename.encode('utf-8') if isinstance(filename, str) else (filename or b'')
        return struct.pack('<I', int(offset)) + name

    async def playback_start(self, filename, offset=0):
        """Start playback of an SD recording. Playback video is delivered on the
        normal video channel, so frames arrive through get_video_frame()."""
        self.log.info('playback start %r @ %s', filename, offset)
        self.last_drw_pkt_at = datetime.datetime.now()
        await self.send_command(
            BinaryCommands.CMD_PEER_PLAYBACK_START, self._playback_payload(filename, offset),
        )
        self.is_video_requested = True
        self._notify_video_state()

    async def playback_stop(self):
        await self.send_command(BinaryCommands.CMD_PEER_PLAYBACK_STOP)
        if self.is_video_requested:
            self.is_video_requested = False
            self._notify_video_state()

    async def playback_pause(self):
        await self.send_command(BinaryCommands.CMD_PEER_PLAYBACK_PAUSE)

    async def playback_resume(self):
        await self.send_command(BinaryCommands.CMD_PEER_PLAYBACK_RESUME)

    async def playback_seek(self, offset):
        await self.send_command(BinaryCommands.CMD_PEER_PLAYBACK_SEEK, struct.pack('<I', int(offset)))

    async def playback_speed(self, speed):
        await self.send_command(BinaryCommands.CMD_PEER_PLAYBACK_SPEED, struct.pack('<i', int(speed)))

    async def playback_step(self):
        await self.send_command(BinaryCommands.CMD_PEER_PLAYBACK_STEP)

    # --- Audio (listen + talk-back) ---------------------------------------
    # The cameras carry 8 kHz G.711 audio. Received audio is decoded to signed
    # 16-bit PCM and published on audio_buffer; talk-back encodes PCM and sends
    # it on the audio DRW channel. Wire framing is derived from the decompiled
    # app and unverified against hardware.

    async def handle_incoming_audio_packet(self, drw_pkt):
        payload = drw_pkt.get_drw_payload()
        # Some firmwares prefix each audio chunk with the same 0x20-byte stream
        # header used for video; strip it when present.
        if payload.startswith(VIDEO_MARKER):
            payload = payload[0x20:]
        if not payload:
            return
        decode = CODECS[self.audio_codec][0]
        try:
            pcm = decode(payload)
        except Exception:
            self.log.debug('Failed to decode audio chunk', exc_info=True)
            return
        await self.audio_buffer.publish(AudioFrame(idx=drw_pkt._cmd_idx, data=pcm))

    async def get_audio_frame(self):
        return await self.audio_buffer.get()

    async def start_audio(self):
        if not self.is_audio_requested:
            self.log.info('start audio')
            await self.send_command(BinaryCommands.CMD_PEER_LIVEAUDIO_START)
            self.is_audio_requested = True

    async def stop_audio(self):
        if self.is_audio_requested:
            self.is_audio_requested = False
            await self.send_command(BinaryCommands.CMD_PEER_LIVEAUDIO_STOP)

    async def start_talk(self):
        """Open the talk-back (speaker) channel."""
        self.log.info('start talk-back')
        await self.send_command(BinaryCommands.CMD_LOCAL_LIVEAUDIO_START)

    async def stop_talk(self):
        await self.send_command(BinaryCommands.CMD_LOCAL_LIVEAUDIO_STOP)

    @staticmethod
    def _make_audio_stream_header(payload_len, codec_byte=0x02):
        # Mirror the 0x20 header the camera itself puts on audio (captured
        # from FTYC): magic, stream type 0x06, codec byte (0x02 observed with
        # A-law), payload length as u32 LE at offset 16, zeros elsewhere.
        hdr = bytearray(0x20)
        hdr[0:4] = VIDEO_MARKER
        hdr[4] = STREAM_TYPE_AUDIO
        hdr[5] = codec_byte
        hdr[16:20] = int(payload_len).to_bytes(4, 'little')
        return bytes(hdr)

    async def send_audio(self, pcm):
        """Send one chunk of signed 16-bit little-endian PCM to the camera
        speaker (encoded with the session codec) on the audio DRW channel.
        The chunk is wrapped in the same 0x20 stream header the camera uses
        for its own audio -- bare G.711 is ignored by the firmware (the
        vendor app frames its talk audio the same way)."""
        encode = CODECS[self.audio_codec][1]
        payload = encode(pcm)
        framed = self._make_audio_stream_header(len(payload)) + payload
        idx = self._outgoing_audio_idx & 0xFFFF
        self._outgoing_audio_idx = (self._outgoing_audio_idx + 1) & 0xFFFF
        await self.send(make_audio_drw_pkt(idx, framed))

    # --- CGI command vocabulary -------------------------------------------
    # Some A9/XD firmwares answer on the CGI (CB_*) command set instead of the
    # BinaryCommands (BINCMD) set. The on-wire distinction between the two
    # vocabularies is not documented in the material this was built from, so
    # these send the CGI opcode through the standard binary DRW frame. This is
    # EXPERIMENTAL and may need adjusting against a CGI-firmware camera; it is
    # provided as a hook so the CgiCommands vocabulary is reachable.

    async def send_cgi_command(self, cgi_cmd: CgiCommands, cmd_payload=b'', *, with_response=False):
        return await self.send_command(cgi_cmd, cmd_payload, with_response=with_response)

    async def cgi_reboot(self):
        await self.send_cgi_command(CgiCommands.CB_IEREBOOT)

    async def cgi_set_ir(self, value):
        await self.send_cgi_command(CgiCommands.CB_IESET_IR, self._onoff_payload(value))

    async def cgi_format_sd(self):
        await self.send_cgi_command(CgiCommands.CB_IEFORMATSD)

    async def cgi_cam_control(self, payload=b''):
        await self.send_cgi_command(CgiCommands.CB_CAM_CONTROL, payload)

    async def setup_device(self):
        auth = await self.login()
        self.dev_properties = await self.get_status()
        self.dev_properties['auth'] = auth
        self.log.info('Camera properties: %s', self.dev_properties)
        self.device_is_ready.set()

    @staticmethod
    def _onoff_payload(value):
        # The *_ONOFF commands carry the desired state as a little-endian int
        # (an IntegerBean in the vendor SDK), so we can set an explicit on/off
        # state instead of blind-toggling.
        return struct.pack('<I', 1 if value else 0)

    async def reboot(self, **kwargs):
        await self.send_command(BinaryCommands.CMD_SYSTEM_REBOOT)

    async def reset(self, **kwargs):
        """Reset to factory defaults via the default-config recovery command."""
        await self.send_command(BinaryCommands.CMD_SYSTEM_DFTCFG_RECOVERY)

    async def toggle_whitelight(self, value, **kwargs):
        self.log.info('white light = %s', value)
        await self.send_command(BinaryCommands.CMD_PEER_LIGHTFILL_ONOFF, self._onoff_payload(value))

    async def toggle_ir(self, value, **kwargs):
        self.log.info('IR = %s', value)
        # IR is also settable through the video-param channel; the dedicated
        # ONOFF command is the direct equivalent of the app's night-mode switch.
        await self.send_command(BinaryCommands.CMD_PEER_IRCUT_ONOFF, self._onoff_payload(value))

    async def toggle_lamp(self, value, **kwargs):
        # Binary cameras expose the fill light as the "lamp"; route it there.
        await self.toggle_whitelight(value, **kwargs)

    async def get_snapshot(self, timeout=5):
        """Request a still image. Returns the raw ACK payload (JPEG bytes on
        cameras that answer inline); exact framing is device-specific."""
        idx = await self.send_command(BinaryCommands.CMD_SNAPSHOT_GET, b'', with_response=True)
        await self.wait_ack(idx)
        return await self.wait_cmd_result(BinaryCommands.CMD_SNAPSHOT_GET, timeout=timeout)

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

    # Preset semantics per YsxLite's live view (cmdPtzPreDo/PreRec/PreChk;
    # the decompile inlines the constants): presets use the PREFAB param
    # type, NOT the PRE_TO/PRE_REC direction values -- those exist in the SDK
    # headers but no vendor app ever sends them.
    #
    # Op mapping verified against YsxLite's own call sites (LiveSingleAty):
    #
    #   cmdPtzPreDo(i, true)  -> cmdPtzSet(1, 0, i)   SET = store preset i
    #   cmdPtzPreDo(i, false) -> cmdPtzSet(1, 4, i)   DEL = delete preset i
    #   (recall path)         -> cmdPtzSet(1, 1, i)   GET = go to preset i
    #   cmdPtzPreChk()        -> cmdPtzSet(1, 3, 0)   CHK = query bitmask
    #   cmdPtzPreRec()        -> cmdPtzSet(1, 2, 0)   REC = mode toggle, index 0
    #
    # Note REC takes index 0 and is paired with a DIRECTION/STOP on the second
    # press, so it toggles a recording mode rather than saving a preset -- it
    # is NOT the "store" op, despite the name. An earlier guess that it was
    # has been backed out.
    #
    # All ops were tried by hand against PTZA and FTYC (2026-08-25): none of
    # them moves the camera or stores anything, so presets are simply not
    # implemented on the tested hardware. The API is kept as best-effort for
    # other firmwares. See VENDOR_APP_FINDINGS.md item 9.

    async def ptz_prefab(self, op, index, **kwargs):
        """Send a raw PREFAB op. `op` is a PtzPrefab (or its int value).

        Exposed so every op can be tried by hand -- the mapping from op to
        store/recall is a hypothesis, not a confirmed fact.
        """
        op = PtzPrefab(int(op))
        self.log.info('PTZ prefab %s index %s', op.name, index)
        data = self._pack_ptz_cmd(PtzParamType.PTZ_PARAM_TYPE_PREFAB, op, index)
        await self.send_command(BinaryCommands.CMD_PASSTHROUGH_STRING_PUT, data)

    async def ptz_goto_preset(self, index, **kwargs):
        """Move to a stored PTZ preset position (1-based index)."""
        await self.ptz_prefab(PtzPrefab.PTZ_PREFAB_GET, index)

    async def ptz_set_preset(self, index, **kwargs):
        """Store the current position as a PTZ preset (1-based index)."""
        await self.ptz_prefab(PtzPrefab.PTZ_PREFAB_SET, index)

    async def ptz_delete_preset(self, index, **kwargs):
        """Delete a stored PTZ preset (1-based index)."""
        self.log.info('delete PTZ preset %s', index)
        data = self._pack_ptz_cmd(PtzParamType.PTZ_PARAM_TYPE_PREFAB, PtzPrefab.PTZ_PREFAB_DEL, index)
        await self.send_command(BinaryCommands.CMD_PASSTHROUGH_STRING_PUT, data)

    async def ptz_query_presets(self, timeout=5):
        """Ask which presets are stored. The vendor app receives a
        (type, param, value) bean back where value is a bitmask with bit
        (n-1) set for stored preset n. Returns the raw ACK payload."""
        data = self._pack_ptz_cmd(PtzParamType.PTZ_PARAM_TYPE_PREFAB, PtzPrefab.PTZ_PREFAB_CHK, 0)
        return await self._request(BinaryCommands.CMD_PASSTHROUGH_STRING_PUT, data, timeout=timeout)

    @staticmethod
    def _pack_ptz_cmd(param_type, param, value) -> bytes:
        # The passthrough PTZ frame is (param_type, param, value): plain moves
        # are (DIRECTION, direction, 0), presets are (PREFAB, op, index).
        data = struct.pack('>III', int(param_type), int(param), int(value))
        return pack_passtrough_cmd(BinaryCommands.CMD_PTZ_SET.value, data)

    def _pack_ptz_dir_cmd(self, ptz: PtzDirection, index: int = 0) -> bytes:
        return self._pack_ptz_cmd(PtzParamType.PTZ_PARAM_TYPE_DIRECTION, int(ptz), index)


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
                 login: str = '', password: str = '',
                 on_video_state_change: Callable[[bool], None] = None) -> Session:
    """Create a session for the camera."""
    session_class = JsonSession if device.is_json else BinarySession
    return session_class(
        device,
        on_disconnect=on_device_lost,
        login=login,
        password=password,
        on_video_state_change=on_video_state_change,
    )
