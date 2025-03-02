from enum import Enum


class Encryption(Enum):
    NONE = 0
    XOR1 = 1


class Channel(Enum):
    Command = 0
    Video = 1
    Audio = 2


class DeviceID:
    def __init__(self, prefix, serial, suffix):
        self.prefix = prefix
        self.serial = serial
        self.suffix = suffix

    def __eq__(self, other):
        return self.dev_id == other.dev_id

    def __hash__(self):
        return hash(self.dev_id)

    def __str__(self):
        return f'DevID({self.dev_id})'

    @property
    def dev_id(self):
        return f'{self.prefix}-{self.serial}-{self.suffix}'


class DeviceDescriptor:
    def __init__(self, dev_id, addr, port, encryption=Encryption.NONE, is_json=False):
        self.dev_id = dev_id
        self.addr = addr
        self.port = port
        self.encryption = encryption
        self.is_json = is_json

    def __eq__(self, other):
        return self.dev_id == other.dev_id

    def __hash__(self):
        return hash((self.dev_id, self.addr, self.port))


class VideoFrame:
    def __init__(self, idx, data):
        self.idx = idx
        self.data = data
