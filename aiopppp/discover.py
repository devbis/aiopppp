import asyncio
import logging
from random import randint

from .const import CAM_MAGIC, PacketType
from .encrypt import ENC_METHODS
from .packets import Packet, parse_packet
from .types import DeviceDescriptor, Encryption

DISCOVERY_PORT = 32108
DEFAULT_DISCOVERY_ADDRESS = '255.255.255.255'

logger = logging.getLogger(__name__)


class DiscoverUDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_receive):
        super().__init__()
        self.on_receive = on_receive

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.on_receive(data, addr)


async def create_udp_server(port, on_receive):
    # Bind to localhost on UDP port
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: DiscoverUDPProtocol(on_receive),
        local_addr=('0.0.0.0', port),
        allow_broadcast=True,
    )
    return transport


class Discovery:
    def __init__(self, remote_addr=DEFAULT_DISCOVERY_ADDRESS, remote_port=DISCOVERY_PORT, local_port=0):
        self.transport = None
        self.remote_addr = remote_addr
        self.remote_port = remote_port
        self.local_port = local_port

    @staticmethod
    def get_possible_discovery_packets():
        unencrypted = Packet(PacketType.LanSearch, b'')
        return [x[1](bytes(unencrypted)) for x in ENC_METHODS.values()]

    @staticmethod
    def maybe_decode(data):
        for enc, dec_enc in ENC_METHODS.items():
            try:
                decoded = dec_enc[0](data)
            except ValueError:
                continue
            if decoded[0] == CAM_MAGIC:
                return enc, decoded
        raise ValueError('Invalid data')

    def on_receive(self, data, addr, callback):
        try:
            encryption, decoded = self.maybe_decode(data)
        except ValueError:
            return

        pkt = parse_packet(decoded)
        logger.debug(f"Received {pkt} from {addr}")

        if pkt.type == PacketType.PunchPkt:
            dev_id = pkt.as_object()
            logger.info(f'found device {dev_id}')
            device = DeviceDescriptor(
                dev_id=dev_id,
                addr=addr[0],
                port=addr[1],
                encryption=encryption,
                is_json=encryption != Encryption.NONE,
            )
            callback(device)

    async def discover(self, callback, period=10):
        assert period >= 1, 'need to wait for camera response more than 1 second'
        logger.info('Start discovery on %s:%d', self.remote_addr, self.remote_port)
        initial_port = self.local_port or randint(0x800, 0xfff0)

        self.transport = await create_udp_server(initial_port, lambda data, addr: self.on_receive(data, addr, callback))
        possible_discovery_packets = self.get_possible_discovery_packets()
        try:
            while True:
                logger.debug(f'sending discovery message {(self.remote_addr, self.remote_port)}')
                for packet in possible_discovery_packets:
                    logger.debug('broadcast> %s', packet.hex(' '))
                    self.transport.sendto(packet, (self.remote_addr, self.remote_port))
                await asyncio.sleep(period)
        finally:
            logger.info('Stop discovery')
            self.transport.close()
            self.transport = None
