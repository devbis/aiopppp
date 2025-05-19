import json
import logging
import struct

from .const import CAM_MAGIC, CC_DEST, BinaryCommands, PacketType
from .types import Channel, DeviceID

logger = logging.getLogger(__name__)

class Packet:
    def __init__(self, typ, payload):
        self.type = typ
        self._payload = payload

    def get_payload(self):
        return self._payload

    def __str__(self):
        return f'{self.type.name}: [{self.get_payload().hex(" ")}]'

    def __bytes__(self):
        payload = self.get_payload()
        return struct.pack('>BBH', CAM_MAGIC, self.type.value, len(payload)) + payload


class PunchPkt(Packet):
    def __str__(self):
        return f'{self.type.name}: [{self.as_object()}]'

    def as_object(self):
        payload = self.get_payload()
        return DeviceID(
            prefix=payload[:4].decode('ascii'),
            serial=str(struct.unpack('>Q', payload[4:12])[0]),
            suffix=payload[12:].rstrip(b'\x00').decode('ascii'),
        )


class DrwPkt(Packet):
    def __init__(self, channel, cmd_idx, drw_payload):
        super().__init__(PacketType.Drw, None)
        self._channel = Channel(channel)
        self._cmd_idx = cmd_idx
        self._payload = drw_payload

    def get_drw_payload(self):
        return self._payload

    def get_payload(self):
        return struct.pack('>BBH', 0xd1, self._channel.value, self._cmd_idx) + self.get_drw_payload()

    def drw_str(self):
        return f'chn:{self._channel.name}, idx: {self._cmd_idx}'

    def __str__(self):
        # return f'{self.type.name}({self.drw_str()}): [{self._payload.hex(" ")}]'
        return f'{self.type.name}({self.drw_str()}): len={len(self._payload)}]'


class JsonCmdPkt(DrwPkt):
    def __init__(self, cmd_idx, json_payload, preamble=b'\x06\x0a\xa0\x80'):
        super().__init__(0, cmd_idx, None)
        self.json_payload = json_payload
        self.preamble = preamble

    def __str__(self):
        return f'{self.type.name}({self.drw_str()}): [{hex(self.preamble[2])}, {self.json_payload}]'

    def get_drw_payload(self):
        payload = json.dumps(self.json_payload).encode('utf-8')
        return self.preamble + len(payload).to_bytes(4, 'little') + payload


def xq_bytes_encode(data, shift):
    new_buf = bytes(b - 1 if b & 1 else b + 1 for b in data)
    return bytes(new_buf[shift:] + new_buf[:shift])


def xq_bytes_decode(data, shift):
    new_buf = bytes(b - 1 if b & 1 else b + 1 for b in data)
    return bytes(new_buf[-shift:] + new_buf[:-shift])

def _inet_btoa(b: bytes) -> str:
    """
    Convert IP Address from byte array to a dot-separated string.
    
    """
    return '.'.join(str(x) for x in b)

def _get_dev_version(b: bytes) -> str:
    """
    Convert 4-byte version number to a string.
    """
    return '.'.join(str(x) for x in reversed(b))

def parse_dev_status(data):
    """
    Example data (len=124):

    "0d 02 01 3d 74 0f 00 00 00 00 00 00 ff ff ff ff bf ff ff ff "
    "01 01 00 30 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
    "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
    "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
    "00 00 00 00 00 00 00 00 00 01 00 00 02 00 00 00 00 00 00 00 "
    "00 00 00 00 00 ff ff ff 00 00 00 00 ff ff ff ff 00 00 00 00 "
    "00 00 00 00"

    Values are unknown so data below is something that looks similar according to the value ranges.

    """

    logger.debug('Parse dev status [%s]', data.hex(' '))

    if len(data) < 124:
        return {}

    (
        sw_ver,              # 0-3 (4 bytes)
        bat_level,           # 4-7 (int)
        time_zone,           # 8-11 (int)
        rec_nmb,             # 12-15 (int)
        sys_uptime,          # 16-19 (int)
        power_supply,        # 20-23 (int)
        dev_name,            # 24-87 (64 bytes)
        sd_status,           # 88 (1 byte)
        p2p_status,          # 89 (1 byte)
        conn_type,           # 90 (1 byte)
        rec_enable_on_start, # 91 (1 byte)
        pic_enable_on_start, # 92 (1 byte)
        ir_cut,              # 93 (1 byte)
        osd_enable,          # 94 (1 byte)
        alarm_enable,        # 95 (1 byte)
        mode,                # 96 (1 byte)
        dhcp,                # 97 (1 byte)
        mac,                 # 98-103 (6 bytes)
        ip_addr_bytes,       # 104-107 (4 bytes)
        netmask_bytes,       # 108-111 (4 bytes)
        pic_nmb,             # 112-115 (int)
        total_size,          # 116-119 (int)
        used_size            # 120-123 (int)
    ) = struct.unpack('<4s5i64s10B6s4s4s3I', data[:124])

    return {
        'tz': f"UTC{time_zone // 3600:+d}", #time zone is in seconds
        'uptime': sys_uptime,
        'dbm': sys_uptime, #not sure if that is wifi dbm or system uptime
        'devName': dev_name.decode('ascii', errors='ignore').rstrip('\0'),
        'sdStatus': sd_status,
        'p2pStatus': p2p_status,
        'connType': conn_type,
        'osdEnable': osd_enable,
        'alarmEnable': alarm_enable,
        'mode': mode,
        'recEnableOnStart': rec_enable_on_start,
        'picEnableOnStart': pic_enable_on_start,
        'recNmb': rec_nmb,
        'picNmb': pic_nmb,
        'totalSize': total_size,
        'usedSize': used_size,
        'powerSupply': power_supply,
        'batLevel': bat_level,
        'dhcp': dhcp,
        'ipAddr': _inet_btoa(ip_addr_bytes),
        'netmask': _inet_btoa(netmask_bytes),
        'mac':mac.hex(':'),
        'mcuver': _get_dev_version(sw_ver),
        'icut': ir_cut,
        'lamp': 0, # lamp is not in the status
    }

class BinaryCmdPkt(DrwPkt):
    START_CMD = b'\x11\x0a'
    HEADER_FORMAT = '<2s3H'

    def __init__(self, cmd_idx, command, cmd_payload, token=b'\x00\x00\x00\x00'):
        super().__init__(0, cmd_idx, None)
        self.command = command
        self.cmd_payload = cmd_payload
        # don't know what is token, but it comes in the beginning of the payload
        # for BATE camera it is always 0x00000000
        self.token = token

    def __str__(self):
        return f'{self.type.name}({self.drw_str()}): {self.command}, (token: {self.token.hex()}) [{self.cmd_payload}]'

    def get_drw_payload(self):
        data = struct.pack(
            self.HEADER_FORMAT,
            self.START_CMD,
            self.command.value,
            len(self.cmd_payload) + len(self.token),
            CC_DEST.get(self.command, 0x0),
        )
        data += self.token
        if self.cmd_payload:
            data += xq_bytes_encode(self.cmd_payload, 4)
        return data

def pack_passtrough_cmd(command, data):
    START_CMD = 0x010A
    HEADER_FORMAT = '>4H4x' # Four 2-byte unsigned shorts, and 4 bytes padding (4x)
    CMD_DEST = 0xFFFF
    SHORT_MASK = 0xFFFF

    header = struct.pack(HEADER_FORMAT, START_CMD, command & SHORT_MASK, (len(data) + 4) & SHORT_MASK, CMD_DEST)
    length = struct.pack('<I', len(header) + len(data))
    return length + header + data

def parse_punch_pkt(data):
    return PunchPkt(PacketType.PunchPkt, data)


def parse_p2prdy_pkt(data):
    return PunchPkt(PacketType.P2pRdy, data)


def make_punch_pkt(dev_id):
    return PunchPkt(
        PacketType.PunchPkt,
        struct.pack(
            '>4sQ8s',
            dev_id.prefix.encode('ascii'),
            int(dev_id.serial),
            dev_id.suffix.encode('ascii'),
        )
    )


def parse_drw_pkt(data):
    channel, cmd_idx = struct.unpack('>xBH', data[:4])
    if data[4:6] == b'\x06\x0a':
        try:
            return JsonCmdPkt(cmd_idx, json.loads(data[12:]), preamble=data[4:8])
        except ValueError:
            logging.warning(f'Failed to parse JSON: {data}')
    elif data[4:6] == b'\x11\x0a':
        try:
            _, command_num, length, dest = struct.unpack(BinaryCmdPkt.HEADER_FORMAT, data[4:12])
            cmd_bin_payload = data[12:]
            token = b'\x00\x00\x00\x00'
            if len(cmd_bin_payload) < 4:
                logging.warning('Binary command payload too short: [%s]', cmd_bin_payload.hex(' '))
            else:
                # assume first 4 bytes is token and other part - xq_encoded payload
                token, cmd_bin_payload = cmd_bin_payload[:4], cmd_bin_payload[4:]
                if len(cmd_bin_payload):
                    cmd_bin_payload = xq_bytes_decode(cmd_bin_payload, 4)
            pkt = BinaryCmdPkt(
                cmd_idx=cmd_idx,
                command=BinaryCommands(command_num),
                token=token,
                cmd_payload=cmd_bin_payload,
            )
            logger.debug('Parsed binary command: %s, raw=[%s]', pkt, data.hex(" "))
            return pkt
        except ValueError:
            logging.warning(f'Failed to parse binary command: {data}')
    return DrwPkt(channel, cmd_idx, data[4:])


def make_drw_ack_pkt(drw_pkt):
    return Packet(
        PacketType.DrwAck,
        struct.pack('>BBHH', 0xd1, drw_pkt._channel.value, 1, drw_pkt._cmd_idx)
    )


def make_p2palive_pkt():
    return Packet(PacketType.P2PAlive, b'')


def make_p2palive_ack_pkt():
    return Packet(PacketType.P2PAliveAck, b'')


def make_close_pkt():
    return Packet(PacketType.Close, b'')


PARSERS = {
    PacketType.PunchPkt: (PunchPkt, parse_punch_pkt),
    PacketType.P2pRdy: (PunchPkt, parse_p2prdy_pkt),
    PacketType.Drw: (DrwPkt, parse_drw_pkt),
}


def parse_packet(data):
    if data[0] != CAM_MAGIC:
        raise ValueError('Invalid data')

    typ, length = struct.unpack('>xBH', data[:4])
    if len(data) != length + 4:
        # some cameras are known to send broken p2p alive packets - zero length, but real length is different and
        # payload consists of zeros
        if typ == PacketType.P2PAlive.value:
            data = data[:4]
        else:
            logger.debug(
                'Invalid pkt length: pkt.len=%d, real length=%d, [%s]',
                length, len(data) - 4, data.hex(' '))

    pkt_class, parse_func = PARSERS.get(PacketType(typ), (Packet, None))
    if parse_func is None:
        return pkt_class(PacketType(typ), data[4:])
    return parse_func(data[4:])
