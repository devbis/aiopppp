import asyncio
import logging

from .const import JSON_COMMAND_NAMES, PTZ, JsonCommands, PacketType
from .encrypt import ENC_METHODS
from .packets import (
    JsonCmdPkt,
    make_drw_ack_pkt,
    make_p2palive_ack_pkt,
    make_punch_pkt,
    parse_packet,
)
from .types import Channel, VideoFrame

logger = logging.getLogger(__name__)


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
        self.frame_queue = asyncio.Queue()
        self.process_video_task = None

        self.video_received = {}
        self.video_boundaries = set()
        self.last_video_frame = -1

    async def process_video_queue(self):
        while True:
            pkt = await self.video_chunk_queue.get()
            await self.handle_incoming_video_packet(pkt)

    def start_video_queue(self):
        self.process_video_task = asyncio.create_task(self.process_video_queue())

    async def handle_incoming_video_packet(self, pkt):
        raise NotImplementedError

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
        logger.info(f".. completeness: {completeness}")

        if complete:
            self.last_video_frame = index
            self.frame_queue.put_nowait(VideoFrame(idx=index, data=b''.join(out)))

            to_delete = [idx for idx in self.video_received.keys() if idx < index]
            for idx in to_delete:
                del self.video_received[idx]
            to_delete = [idx for idx in self.video_boundaries if idx < index]
            for idx in to_delete:
                self.video_boundaries.remove(idx)


class Session(PacketQueueMixin, VideoQueueMixin):
    def __init__(self, dev, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dev = dev
        self.outgoing_command_idx = 0
        self.transport = None
        self.ready_counter = 0
        self.info_requested = False
        self.video_requested = False

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

    async def send(self, pkt):
        logger.debug(f"send> {pkt}")
        encoded_pkt = ENC_METHODS[self.dev.encryption][1](bytes(pkt))
        self.transport.sendto(encoded_pkt, (self.dev.addr, self.dev.port))

    async def handle_incoming_packet(self, pkt):
        # logger.debug('process %s', pkt)
        if pkt.type == PacketType.PunchPkt:
            pass
        if pkt.type == PacketType.P2pRdy:
            self.ready_counter += 1
            if self.ready_counter == 5:
                await self.login()
        elif pkt.type == PacketType.P2PAlive:
            await self.send(make_p2palive_ack_pkt())
        elif pkt.type == PacketType.Drw:
            await self.handle_drw(pkt)
        elif pkt.type == PacketType.DrwAck:
            logger.warning(f'Got DRW ACK {pkt}')

    async def login(self):
        pass

    async def request_video(self, mode):
        """
        Mode is 1 for 640x480 or 2 for 320x240
        """
        pass

    async def handle_drw(self, pkt):
        pass

    async def _run(self):
        self.transport = await self.create_udp()
        self.start_packet_queue()
        self.start_video_queue()

        await self.send(make_punch_pkt(self.dev.dev_id))

        # send punch packet
        while True:
            await self.loop_step()
            await asyncio.sleep(0.2)

    async def loop_step(self):
        # await self.send(make_punch_pkt(self.dev.dev_id))
        print(f"iterate in Session for {self.dev.dev_id}")

    def start(self):
        return asyncio.create_task(self._run())


class JsonSession(Session):
    COMMON_DATA = {
        'user': "admin",
        'pwd': "6666",
        'devmac': "0000"
    }

    async def send_command(self, cmd, **kwargs):
        data = {
            'pro': JSON_COMMAND_NAMES[cmd],
            'cmd': cmd.value,
        }
        pkt = JsonCmdPkt(self.outgoing_command_idx, {**data, **kwargs, **self.COMMON_DATA})
        self.outgoing_command_idx += 1
        await self.send(pkt)

    async def login(self):
        await self.send_command(JsonCommands.CMD_CHECK_USER)
        await asyncio.sleep(0.2)
        await self.send_command(JsonCommands.CMD_GET_PARMS)
        self.info_requested = True

    async def request_video(self, mode):
        await self.send_command(JsonCommands.CMD_STREAM, video=mode)

    async def handle_drw(self, drw_pkt):
        logger.debug('handle_drw(idx=%s)', drw_pkt._cmd_idx)
        await self.send(make_drw_ack_pkt(drw_pkt))
        if drw_pkt._channel == Channel.Video:
            # logger.debug(f'Got video data {drw_pkt.get_drw_payload()}')
            self.video_chunk_queue.put_nowait(drw_pkt)

    async def handle_incoming_video_packet(self, pkt):
        video_payload = pkt.get_drw_payload()
        # logger.info(f'- video frame {pkt._cmd_idx}')

        # 0x20 - size of the header starting with this magic
        if video_payload.startswith(b'\x55\xaa\x15\xa8\x03'):
            self.video_boundaries.add(pkt._cmd_idx)
            self.video_received[pkt._cmd_idx] = video_payload[0x20:]
        else:
            self.video_received[pkt._cmd_idx] = video_payload
        await self.process_video_frame()

    async def loop_step(self):
        if self.info_requested and not self.video_requested:
            self.video_requested = True
            await self.request_video(1)

    async def control(self, **kwargs):
        await self.send_command(JsonCommands.CMD_DEV_CONTROL, **kwargs)

    async def toggle_lamp(self, value):
        await self.control(lamp=1 if value else 0)

    async def toggle_ir(self, value):
        await self.control(icut=1 if value else 0)

    async def rotate_start(self, value):
        value = PTZ[f'{value.upper()}_START'].value
        await self.send_command(JsonCommands.CMD_PTZ_CONTROL, parms=0, value=value)

    async def rotate_stop(self, **kwargs):
        for value in [PTZ.LEFT_STOP, PTZ.RIGHT_STOP, PTZ.DOWN_STOP, PTZ.UP_STOP]:
            await self.send_command(JsonCommands.CMD_PTZ_CONTROL, parms=0, value=value.value)

    async def step_rotate(self, value):
        await self.rotate_start(value)
        await asyncio.sleep(0.1)
        await self.rotate_stop()

    async def reboot(self, **kwargs):
        await self.control(reboot=1)

    async def reset(self, **kwargs):
        """
        Reset to factory defaults
        """
        await self.control(reset=1)
