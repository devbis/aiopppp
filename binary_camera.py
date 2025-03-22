import asyncio
import struct

import aiopppp.const
from aiopppp.packets import (
    make_punch_pkt,
    make_p2palive_pkt,
    parse_drw_pkt,
    make_drw_ack_pkt,
    BinaryCmdPkt,
    xq_bytes_decode,
    DrwPkt,
)
from aiopppp.types import DeviceID


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
    def __init__(self):
        self.transport = None
        self.dev_id = DeviceID('TEST',123456, 'CAMERA')
        self.input = asyncio.Queue()
        self.output = asyncio.Queue()
        self.client_addr = None
        self.ticket = b'abcd'
        self.cmd_idx = 1

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

            if cmd_id == aiopppp.const.BinaryCommands.ConnectUser:
                INCORRECT_USER_RESP = '11 0a 20 11 0c 00 ff 00 00 00 00 00 57 56 6c 37 fe 01 01 01'
                CORRECT_USER_RESP = '11 0a 20 11 04 00 ff 00 0e fc ff ff'

                username, password = struct.unpack('<32s128s', data)
                username = username.decode('utf-8').strip('\x00')
                password = password.decode('utf-8').strip('\x00')

                resp = CORRECT_USER_RESP if username == 'admin' and password == 'admin' else INCORRECT_USER_RESP

                print('... BinaryCommand: cmd_id:', cmd_id, 'data:', data)
                await asyncio.sleep(0.3)
                print('... send ConnectUserAck')
                self.output.put_nowait((bytes(
                    DrwPkt(cmd_idx=0, channel=0, drw_payload=bytes.fromhex(resp)),
                ), self.client_addr))
                # self.output.put_nowait((bytes(BinaryCmdPkt(
                #     cmd_idx=self.cmd_idx,
                #     command=aiopppp.const.BinaryCommands.ConnectUserAck,
                #     ticket=self.ticket,
                #     cmd_payload=b'\xff\x00\x00\x00',
                # )), self.client_addr))
                self.cmd_idx += 1
            elif cmd_id == aiopppp.const.BinaryCommands.DevStatus:
                await asyncio.sleep(0.3)
                print('... send DevStatusAck')
                self.output.put_nowait(
                    (
                        bytes(
                            BinaryCmdPkt(
                                cmd_idx=self.cmd_idx,
                                command=aiopppp.const.BinaryCommands.DevStatusAck,
                                # token=b'\x0a\xfc\xff\xff',
                                token=b"\x00\x00\x00\x00",
                                # cmd_payload=bytes(range(0x15)),
                                cmd_payload=bytes.fromhex(
                                    "0d 02 01 3d 74 0f 00 00 00 00 00 00 ff ff ff ff bf ff ff ff "
                                    "01 01 00 30 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
                                    "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
                                    "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
                                    "00 00 00 00 00 00 00 00 00 01 00 00 02 00 00 00 00 00 00 00 "
                                    "00 00 00 00 00 ff ff ff 00 00 00 00 ff ff ff ff 00 00 00 00 "
                                    "00 00 00 00",
                                ),
                            )
                        ),
                        self.client_addr,
                    )
                )


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
        self.transport = await create_udp_server(32108, self.on_receive)
        out_t = asyncio.create_task(self.send_task())
        in_t = asyncio.create_task(self.receive_task())
        await asyncio.gather(*[out_t, in_t])


async def main():
    camera = BinaryCamera()
    await camera.run()

asyncio.run(main())
