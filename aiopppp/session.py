import asyncio
import logging

from .const import PacketType, JSON_COMMAND_NAMES, JsonCommands
from .encrypt import ENC_METHODS
from .packets import make_punch_pkt, parse_packet, make_p2palive_ack_pkt, JsonCmdPkt, Packet, make_drw_ack_pkt

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
        self.queue = asyncio.Queue()
        self.process_task = None

    async def process_queue(self):
        while True:
            pkt = await self.queue.get()
            await self.handle_incoming_packet(pkt)

    def start_queue(self):
        self.process_task = asyncio.create_task(self.process_queue())

    async def handle_incoming_packet(self, pkt):
        raise NotImplementedError


class Session(PacketQueueMixin):
    def __init__(self, dev, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dev = dev
        self.transport = None
        self.ready_counter = 0
        self.outgoing_command_idx = 0

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
        logger.debug(f"recv< {pkt} {pkt.get_payload()}")
        self.queue.put_nowait(pkt)

    async def send(self, pkt):
        logger.debug(f"send> {pkt}")
        encoded_pkt = ENC_METHODS[self.dev.encryption][1](bytes(pkt))
        self.transport.sendto(encoded_pkt, (self.dev.addr, self.dev.port))

    async def handle_incoming_packet(self, pkt):
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

    async def login(self):
        pass

    async def handle_drw(self, pkt):
        pass

    async def _run(self):
        self.transport = await self.create_udp()
        self.start_queue()

        await self.send(make_punch_pkt(self.dev.dev_id))

        # send punch packet
        while True:
            await asyncio.sleep(5)
            await self.send(make_punch_pkt(self.dev.dev_id))
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

    async def handle_drw(self, drw_pkt):
        await self.send(make_drw_ack_pkt(drw_pkt))
