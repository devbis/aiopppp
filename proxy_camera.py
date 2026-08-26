"""Transparent PPPP proxy ("man-in-the-middle") camera for the binary protocol.

Advertises a configurable DID to the app and forwards every packet to a real
camera (and its replies back), rewriting only the DID so the app talks to the
proxy while the proxy talks to the real device. Each forwarded control packet is
logged with its raw bytes and, when recognised, its decoded form. Video and
audio stream packets are relayed but never logged.

Typical use (app configured with the proxy DID):

    python proxy_camera.py --did PROX-000001-CAMERA --target-ip 192.168.1.50

The app then discovers/connects to this host using PROX-000001-CAMERA, and all
traffic is relayed to the camera at 192.168.1.50 (whose real DID is learned from
its discovery reply, or given with --target-did).

Only the binary protocol is supported: those cameras use no transport
encryption, so the DID can be rewritten directly on the wire. JSON (XOR1)
cameras would need decrypt/re-encrypt and are out of scope.
"""

import argparse
import asyncio
import logging
import struct

from aiopppp.const import CAM_MAGIC, PacketType
from aiopppp.packets import PunchPkt, parse_packet
from aiopppp.types import Channel, DeviceID

logger = logging.getLogger('proxy_camera')

# Packet types whose payload carries the 20-byte packed DID.
_DID_TYPES = {
    PacketType.PunchPkt.value,
    PacketType.P2pRdy.value,
    PacketType.PunchTo.value,
}
_STREAM_CHANNELS = {Channel.Video.value, Channel.Audio.value}
_KEEPALIVE_TYPES = {PacketType.P2PAlive.value, PacketType.P2PAliveAck.value}


def parse_did(text):
    """Parse a 'PREFIX-SERIAL-SUFFIX' DID string into a DeviceID."""
    parts = text.split('-')
    if len(parts) < 3:
        raise ValueError(f'Invalid DID {text!r}, expected PREFIX-SERIAL-SUFFIX')
    prefix, serial, suffix = parts[0], parts[1], parts[2]
    return DeviceID(prefix=prefix, serial=serial, suffix=suffix)


def pack_did(dev_id):
    """Return the 20-byte on-wire form of a DID (as carried in PunchPkt)."""
    return struct.pack(
        '>4sQ8s',
        dev_id.prefix.encode('ascii'),
        int(dev_id.serial),
        dev_id.suffix.encode('ascii'),
    )


class _EndpointProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_receive):
        self._on_receive = on_receive

    def datagram_received(self, data, addr):
        self._on_receive(data, addr)


class ProxyCamera:
    """Relay between an app and a real binary-protocol camera, rewriting the DID."""

    def __init__(self, proxy_did, target_ip, target_port=32108,
                 target_did=None, listen_host='0.0.0.0', listen_port=32108,
                 log_keepalive=False):
        self.proxy_did = proxy_did
        self.proxy_packed = pack_did(proxy_did)
        self.target_ip = target_ip
        self.camera_addr = (target_ip, target_port)
        self.real_did = target_did
        self.real_packed = pack_did(target_did) if target_did else None
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.log_keepalive = log_keepalive

        self.app_transport = None
        self.camera_transport = None
        # The app's current source address (differs between discovery and the
        # session); replies are sent to the most recent one.
        self.app_addr = None

    async def run(self):
        loop = asyncio.get_running_loop()
        # App-facing socket: the app discovers/connects here.
        self.app_transport, _ = await loop.create_datagram_endpoint(
            lambda: _EndpointProtocol(self._on_app_packet),
            local_addr=(self.listen_host, self.listen_port),
            allow_broadcast=True,
        )
        # Camera-facing socket: we talk to the real camera from here.
        self.camera_transport, _ = await loop.create_datagram_endpoint(
            lambda: _EndpointProtocol(self._on_camera_packet),
            remote_addr=self.camera_addr,
        )
        logger.info('Proxy DID %s  ->  camera %s:%d (real DID %s)',
                    self.effective_proxy_did().dev_id, self.camera_addr[0], self.camera_addr[1],
                    self.real_did.dev_id if self.real_did else '<unknown, will learn>')
        # The serial travels as a uint64, so leading zeros are dropped on the
        # wire. Tell the user the exact DID to configure the app with.
        effective = self.effective_proxy_did().dev_id
        if effective != self.proxy_did.dev_id:
            logger.info('NOTE: configure the app with DID %s (serial leading zeros are dropped)',
                        effective)
        else:
            logger.info('Configure the app with DID %s', effective)
        logger.info('Listening for the app on %s:%d', self.listen_host, self.listen_port)
        # Run until cancelled.
        await asyncio.Event().wait()

    # -- packet handlers ----------------------------------------------------

    def _on_app_packet(self, data, addr):
        self.app_addr = addr
        self._log('APP->CAM', data)
        forwarded = self._rewrite(data, self.proxy_packed, self.real_packed)
        self.camera_transport.sendto(forwarded)

    def _on_camera_packet(self, data, addr):
        # Learn the real DID from the camera's first PunchPkt so app->camera
        # DID rewriting works even without --target-did.
        if self.real_packed is None and len(data) >= 2 and data[1] == PacketType.PunchPkt.value:
            self._learn_real_did(data)
        self._log('CAM->APP', data)
        forwarded = self._rewrite(data, self.real_packed, self.proxy_packed)
        if self.app_addr is not None:
            self.app_transport.sendto(forwarded, self.app_addr)

    def _learn_real_did(self, data):
        try:
            self.real_did = PunchPkt(PacketType.PunchPkt, data[4:]).as_object()
            self.real_packed = pack_did(self.real_did)
            logger.info('Learned real camera DID: %s', self.real_did.dev_id)
        except Exception:
            logger.debug('Could not parse camera DID from PunchPkt', exc_info=True)

    # -- helpers ------------------------------------------------------------

    def effective_proxy_did(self):
        """The proxy DID as it appears on the wire (serial normalized to uint64)."""
        return PunchPkt(PacketType.PunchPkt, self.proxy_packed).as_object()

    @staticmethod
    def _rewrite(data, old_packed, new_packed):
        """Return data with the DID rewritten, only in DID-bearing packets."""
        if not old_packed or not new_packed or old_packed == new_packed:
            return data
        if len(data) >= 2 and data[1] in _DID_TYPES and old_packed in data:
            return data.replace(old_packed, new_packed)
        return data

    @staticmethod
    def _is_stream(data):
        """True for video/audio DRW (and their ACKs), which must not be logged."""
        if len(data) < 6 or data[0] != CAM_MAGIC:
            return False
        if data[1] in (PacketType.Drw.value, PacketType.DrwAck.value):
            return data[5] in _STREAM_CHANNELS
        return False

    def _log(self, direction, data):
        if self._is_stream(data):
            return
        if not self.log_keepalive and len(data) >= 2 and data[1] in _KEEPALIVE_TYPES:
            return
        try:
            decoded = str(parse_packet(data))
        except Exception:
            typ = f'0x{data[1]:02x}' if len(data) >= 2 else '??'
            decoded = f'<undecodable type={typ}>'
        logger.info('%s | %s', direction, decoded)
        logger.info('%s |   raw: %s', direction, data.hex(' '))


def _build_arg_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--did', required=True,
                   help='DID to advertise to the app (PREFIX-SERIAL-SUFFIX)')
    p.add_argument('--target-ip', required=True,
                   help='IP (or broadcast address) of the real camera')
    p.add_argument('--target-port', type=int, default=32108,
                   help='UDP port of the real camera (default 32108)')
    p.add_argument('--target-did', default=None,
                   help="Real camera DID; if omitted it is learned from the camera's "
                        'discovery reply')
    p.add_argument('--listen-host', default='0.0.0.0',
                   help='Local address to listen on for the app (default 0.0.0.0)')
    p.add_argument('--listen-port', type=int, default=32108,
                   help='Local UDP port to listen on for the app (default 32108)')
    p.add_argument('--log-keepalive', action='store_true',
                   help='Also log P2PAlive/P2PAliveAck keepalives (noisy)')
    p.add_argument('--log-level', default='INFO')
    return p


async def main(argv=None):
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format='%(message)s')
    proxy = ProxyCamera(
        proxy_did=parse_did(args.did),
        target_ip=args.target_ip,
        target_port=args.target_port,
        target_did=parse_did(args.target_did) if args.target_did else None,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        log_keepalive=args.log_keepalive,
    )
    await proxy.run()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
