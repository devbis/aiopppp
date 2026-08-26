import asyncio
import struct

import aiopppp.const
from aiopppp.const import BinaryCommands, LibError
from aiopppp.session import BinarySession
from aiopppp.packets import (
    make_punch_pkt,
    make_p2palive_pkt,
    parse_drw_pkt,
    make_drw_ack_pkt,
    BinaryCmdPkt,
    xq_bytes_decode,
    DrwPkt,
)
from aiopppp.types import Channel, DeviceID

VIDEO_MARKER = b'\x55\xaa\x15\xa8'

# A minimal, structurally-valid JPEG (SOI ... EOI). Content is irrelevant to the
# protocol path; it just needs the FFD8..FFD9 envelope so consumers see a frame.
_MINI_JPEG = bytes.fromhex(
    'ffd8ffe000104a46494600010100000100010000'
    'ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c28372'
    '92c30313434341f27393d38323c2e333432'
    'ffc0000b080010001001011100'
    'ffc4001f0000010501010101010100000000000000000102030405060708090a0b'
    'ffc400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552'
    'd1f02433627282090a161718191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a'
    '737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9c'
    'ad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9fa'
    'ffda0008010100003f00fbd0ffd9'
)


class UDPProtocol(asyncio.DatagramProtocol):
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
        lambda: UDPProtocol(on_receive),
        local_addr=('127.0.0.1', port),
        allow_broadcast=True,
    )
    return transport


class BinaryCamera:
    # The only commands a real camera refuses without a login, and the code each
    # answers with. Confirmed on PTZA fw 2.2.15.93: everything else -- video,
    # audio, PTZ, lights, the status/info/datetime/wifi-settings reads, video
    # params (get and set) and time sync -- works unauthenticated.
    PRIVILEGED = {
        BinaryCommands.CMD_SYSTEM_REBOOT: LibError.USER_NO_PRIVILEGE,
        BinaryCommands.CMD_NET_WIFI_SCAN: LibError.CMD_EXCUTE_FAILED,
        BinaryCommands.CMD_SYSTEM_USER_GET: LibError.CMD_EXCUTE_FAILED,
    }

    def __init__(self, port=32108, dev_id=None, auth_mode='normal'):
        self.transport = None
        self.port = port
        self.dev_id = dev_id or DeviceID('TEST',123456, 'CAMERA')
        self.input = asyncio.Queue()
        self.output = asyncio.Queue()
        self.client_addr = None
        # Synthetic session ticket, handed out in the USER_CHK reply payload.
        self.ticket = b'\xde\xad\xbe\xef'
        # How much an unauthenticated session is allowed:
        #   'normal' - refuse only PRIVILEGED, as real hardware does
        #   'stuck'  - refuse everything with UNAUTH, video included; a camera
        #              that has wedged itself, cleared by a power cycle
        #   'off'    - no auth checks at all
        self.auth_mode = auth_mode
        self.logged_in = False
        self.cmd_idx = 1
        self.video_task = None
        self.video_idx = 1
        self.frame_period = 0.2  # ~5 fps of synthetic frames

    def on_receive(self, data, addr):
        # print(f"Received {data} from {addr}")
        if data[0] == aiopppp.const.CAM_MAGIC:
            self.input.put_nowait((data, addr))

    async def send_task(self):
        while True:
            data, addr = await self.output.get()
            assert isinstance(data, bytes), 'Data must be bytes'
            if data[1] in [aiopppp.const.PacketType.PunchPkt.value, aiopppp.const.PacketType.P2PAlive.value]:
                print(f'Send {data} to {addr}')
                self.transport.sendto(data, addr)
            elif self.client_addr:
                print(f'Send {data}')
                self.transport.sendto(data, self.client_addr)
            self.output.task_done()

    async def send_p2p_rdy_set(self):
        pkt = make_punch_pkt(self.dev_id)
        pkt.type = aiopppp.const.PacketType.P2pRdy
        for _ in range(10):
            self.output.put_nowait((bytes(pkt), self.client_addr))
            await asyncio.sleep(0.1)

    _STATUS_BLOB = bytes.fromhex(
        "0d 02 01 3d 74 0f 00 00 00 00 00 00 ff ff ff ff bf ff ff ff "
        "01 01 00 30 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
        "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
        "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
        "00 00 00 00 00 00 00 00 00 01 00 00 02 00 00 00 00 00 00 00 "
        "00 00 00 00 00 ff ff ff 00 00 00 00 ff ff ff ff 00 00 00 00 "
        "00 00 00 00".replace(' ', '')
    )

    def _send_cmd_ack(self, ack_command, cmd_payload=b'', token=None):
        # The token field of a reply is the result code, so a success carries
        # LibError.OK there and puts its data in the payload.
        self.output.put_nowait((
            bytes(BinaryCmdPkt(
                cmd_idx=self.cmd_idx,
                command=ack_command,
                token=struct.pack('<i', LibError.OK) if token is None else token,
                cmd_payload=cmd_payload,
            )),
            self.client_addr,
        ))
        self.cmd_idx += 1

    def _send_cmd_refusal(self, ack_command, code=LibError.UNAUTH):
        """Refuse the way real hardware does: no payload, negative code in the
        4-byte token field."""
        print(f'... refusing {ack_command.name}: {code.name} ({code.value})')
        self._send_cmd_ack(ack_command, b'', token=struct.pack('<i', code.value))

    async def process_drw(self, data):
        cmd_header_len = 12
        pkt = parse_drw_pkt(data[4:])
        self.output.put_nowait((bytes(make_drw_ack_pkt(pkt)), self.client_addr))
        payload = pkt.get_drw_payload()
        # print('... DRW payload:', payload)
        if payload[:2] == BinaryCmdPkt.START_CMD:
            cmd_id = aiopppp.const.BinaryCommands(int.from_bytes(payload[2:4], "little"))
            data = payload[cmd_header_len:]
            if len(data) > 4:
                data = xq_bytes_decode(data, 4)

            if cmd_id == BinaryCommands.CMD_SYSTEM_USER_CHK:
                username, password = struct.unpack('<32s128s', data)
                username = username.decode('utf-8').strip('\x00')
                password = password.decode('utf-8').strip('\x00')
                print('... USER_CHK:', username, password)
                await asyncio.sleep(0.1)
                if username == 'admin' and password == 'admin':
                    # cmd_payload[4:8] is the session ticket the client will echo.
                    self.logged_in = True
                    self._send_cmd_ack(
                        BinaryCommands.ACK_SYSTEM_USER_CHK,
                        b'\x00\x00\x00\x00' + self.ticket,
                    )
                else:
                    # PTZA answers bad credentials with -1010, not the -1013
                    # USER_PWD_INCORRECT the vendor enum also defines.
                    self.logged_in = False
                    self._send_cmd_refusal(
                        BinaryCommands.ACK_SYSTEM_USER_CHK, LibError.CMD_EXCUTE_FAILED,
                    )
            elif (
                self.auth_mode != 'off' and not self.logged_in
                and (self.auth_mode == 'stuck' or cmd_id in self.PRIVILEGED)
            ):
                code = LibError.UNAUTH if self.auth_mode == 'stuck' else self.PRIVILEGED[cmd_id]
                ack = BinarySession.ACKS.get(cmd_id)
                if ack is None:
                    print('... unauthenticated, unhandled command:', cmd_id)
                else:
                    self._send_cmd_refusal(ack, code)
            elif cmd_id == BinaryCommands.CMD_SYSTEM_STATUS_GET:
                await asyncio.sleep(0.1)
                self._send_cmd_ack(BinaryCommands.ACK_SYSTEM_STATUS_GET, self._STATUS_BLOB)
            elif cmd_id == BinaryCommands.CMD_PEER_VIDEOPARAM_SET:
                self._send_cmd_ack(BinaryCommands.ACK_PEER_VIDEOPARAM_SET)
            elif cmd_id == BinaryCommands.CMD_PEER_VIDEOPARAM_GET:
                # Real cameras (PTZA) ignore the requested id and answer with
                # the full table of params 1..12: resolution=HD, ircut=1.
                table = [0] * 12
                table[0] = 2  # resolution -> HD
                table[8] = 1  # ircut on
                self._send_cmd_ack(BinaryCommands.ACK_PEER_VIDEOPARAM_GET, struct.pack('<12I', *table))
            elif cmd_id == BinaryCommands.CMD_SYSTEM_DATETIME_GET:
                # PTZA layout: u32 UTC epoch, i32 tz seconds west, pad, ntp[64]
                self._send_cmd_ack(
                    BinaryCommands.ACK_SYSTEM_DATETIME_GET,
                    struct.pack('<Ii8x64s', 1787493648, -7200, b'time.windows.com'),
                )
            elif cmd_id == BinaryCommands.CMD_SYSTEM_DATETIME_SET:
                print('... DATETIME_SET ->', data.hex(' '))
                self._send_cmd_ack(BinaryCommands.ACK_SYSTEM_DATETIME_SET)
            elif cmd_id == BinaryCommands.CMD_NET_WIFISETTING_GET:
                # PTZA layout: mode, pad12, security, pad4, ssid[32],
                # password[128], five char[16] dotted-quad strings
                wifi = struct.pack(
                    '<I12xI4x32s128s16s16s16s16s16s',
                    1, 4, b'TESTNET', b'12345678',
                    b'0.0.0.0', b'', b'', b'', b'',
                )
                self._send_cmd_ack(BinaryCommands.ACK_NET_WIFISETTING_GET, wifi)
            elif cmd_id == BinaryCommands.CMD_SYSTEM_USER_GET:
                # account[32] + password[128], vendor-app layout
                self._send_cmd_ack(
                    BinaryCommands.ACK_SYSTEM_USER_GET,
                    struct.pack('<32s128s', b'admin', b'admin'),
                )
            elif cmd_id == BinaryCommands.CMD_NET_WIFI_SCAN:
                self._send_cmd_ack(
                    BinaryCommands.ACK_NET_WIFI_SCAN,
                    struct.pack('<32s', b'TESTNET') + struct.pack('<32s', b'NEIGHBOR-AP'),
                )
            elif cmd_id == BinaryCommands.CMD_SYSTEM_INF_GET:
                self._send_cmd_ack(
                    BinaryCommands.ACK_SYSTEM_INF_GET,
                    bytes.fromhex('5d0f0202401f0000') + b'\x00' * 520,
                )
            elif cmd_id == BinaryCommands.CMD_PEER_LIVEVIDEO_START:
                self._send_cmd_ack(BinaryCommands.ACK_PEER_LIVEVIDEO_START)
                self._start_video()
            elif cmd_id == BinaryCommands.CMD_PEER_LIVEVIDEO_STOP:
                self._send_cmd_ack(BinaryCommands.ACK_PEER_LIVEVIDEO_STOP)
                self._stop_video()
            elif cmd_id == BinaryCommands.CMD_PEER_IRCUT_ONOFF:
                print('... IRCUT ->', data.hex(' '))
                self._send_cmd_ack(BinaryCommands.ACK_PEER_IRCUT_ONOFF)
            elif cmd_id == BinaryCommands.CMD_PEER_LIGHTFILL_ONOFF:
                print('... LIGHTFILL ->', data.hex(' '))
                self._send_cmd_ack(BinaryCommands.ACK_PEER_LIGHTFILL_ONOFF)
            elif cmd_id == BinaryCommands.CMD_SNAPSHOT_GET:
                self._send_cmd_ack(BinaryCommands.ACK_SNAPSHOT_GET, _MINI_JPEG)
            elif cmd_id == BinaryCommands.CMD_SYSTEM_REBOOT:
                print('... REBOOT requested')
                self._send_cmd_ack(BinaryCommands.ACK_SYSTEM_REBOOT)
            elif cmd_id == BinaryCommands.CMD_PASSTHROUGH_STRING_PUT:
                print('... PTZ/passthrough ->', data.hex(' '))
                self._send_cmd_ack(BinaryCommands.ACK_PASSTHROUGH_STRING_PUT)
            else:
                print('... unhandled command:', cmd_id)

    def _start_video(self):
        if self.video_task is None or self.video_task.done():
            print('... start video stream')
            self.video_task = asyncio.create_task(self._stream_video())

    def _stop_video(self):
        if self.video_task and not self.video_task.done():
            print('... stop video stream')
            self.video_task.cancel()
            self.video_task = None

    def _next_video_idx(self):
        idx = self.video_idx
        self.video_idx = (self.video_idx + 1) & 0xFFFF
        return idx

    def _send_video_chunk(self, chunk):
        pkt = DrwPkt(channel=Channel.Video.value, cmd_idx=self._next_video_idx(), drw_payload=chunk)
        self.output.put_nowait((bytes(pkt), self.client_addr))

    async def _stream_video(self):
        try:
            while True:
                # First chunk carries the 0x20-byte frame header (marker + pad);
                # the client strips it and treats this index as a frame boundary.
                header = VIDEO_MARKER + b'\x00' * (0x20 - len(VIDEO_MARKER))
                body = _MINI_JPEG
                # Split into ~1024-byte payloads across several DRW chunks.
                step = 1024
                parts = [body[i:i + step] for i in range(0, len(body), step)] or [b'']
                self._send_video_chunk(header + parts[0])
                for part in parts[1:]:
                    self._send_video_chunk(part)
                await asyncio.sleep(self.frame_period)
        except asyncio.CancelledError:
            raise


    async def on_packet(self, data, addr):
        print(f"Received packet: {data} {addr}")
        if data[1] == aiopppp.const.PacketType.LanSearch.value:
            print('Received LanSearch packet')
            self.output.put_nowait((bytes(make_punch_pkt(self.dev_id)), addr))
        if data[1] == aiopppp.const.PacketType.PunchPkt.value:
            self.client_addr = addr
            print('Received PunchPkt packet, starting p2prdy sending')
            asyncio.create_task(self.send_p2p_rdy_set())
            # self.output.put_nowait((bytes(make_punch_pkt(self.dev_id)), addr
        elif data[1] == aiopppp.const.PacketType.P2PAlive.value:
            self.output.put_nowait((bytes(make_p2palive_pkt()), addr))
        elif data[1] == aiopppp.const.PacketType.Drw.value:
            asyncio.create_task(self.process_drw(data))

    async def receive_task(self):
        while True:
            data, addr = await self.input.get()
            await self.on_packet(data, addr)
            self.input.task_done()

    async def run(self):
        self.transport = await create_udp_server(self.port, self.on_receive)
        out_t = asyncio.create_task(self.send_task())
        in_t = asyncio.create_task(self.receive_task())
        await asyncio.gather(*[out_t, in_t])


async def main():
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 32108
    # binary_camera.py [port] [normal|stuck|off]
    auth_mode = sys.argv[2] if len(sys.argv) > 2 else 'normal'
    print(f'Listening on {port}, auth mode: {auth_mode}')
    camera = BinaryCamera(port=port, auth_mode=auth_mode)
    await camera.run()


if __name__ == '__main__':
    asyncio.run(main())
