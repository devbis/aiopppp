import asyncio
import logging
from random import randint

from .const import CAM_MAGIC, PacketType
from .encrypt import ENC_METHODS
from .packets import Packet, parse_packet
from .types import Device

DISCOVERY_PORT = 32108

logger = logging.getLogger(__name__)


class DiscoverUDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_receive):
        super().__init__()
        self.on_receive = on_receive

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        # message = data.decode()
        self.on_receive(data, addr)


async def create_udp_server(port, on_receive):
    # Bind to localhost on UDP port 8888
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: DiscoverUDPProtocol(on_receive),
        local_addr=('0.0.0.0', port),
        allow_broadcast=True,
    )
    return transport


class Discovery:
    def __init__(self):
        self.devices = {}
        self.transport = None

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
            device = Device(
                dev_id=dev_id,
                addr=addr[0],
                port=addr[1],
                encryption=encryption,
            )

            if device.dev_id not in self.devices:
                self.devices[device.dev_id] = device
                callback(device)

    async def discover(self, callback):
        logger.warning('start discovery')
        initial_port = randint(2000, 40000)

        self.transport = await create_udp_server(initial_port, lambda data, addr: self.on_receive(data, addr, callback))
        possible_discovery_packets = self.get_possible_discovery_packets()
        try:
            while True:
                logger.warning(f'sending discovery message {("255.255.255.255", DISCOVERY_PORT)}')
                for packet in possible_discovery_packets:
                    logger.debug('broadcast> %s', packet.hex(' '))
                    self.transport.sendto(packet, ('255.255.255.255', DISCOVERY_PORT))

                await asyncio.sleep(5)
        finally:
            logger.warning('end discovery')
            self.transport.close()
            self.transport = None
