from enum import Enum

CAM_MAGIC = 0xf1


class PacketType(Enum):
    Close = 0xf0
    LanSearchExt = 0x32
    LanSearch = 0x30
    P2PAlive = 0xe0
    P2PAliveAck = 0xe1
    Hello = 0x00
    P2pRdy = 0x42
    P2pReq = 0x20
    LstReq = 0x67
    DrwAck = 0xd1
    Drw = 0xd0

    # From CSession_CtrlPkt_Proc incomplete
    PunchTo = 0x40
    PunchPkt = 0x41
    HelloAck = 0x01
    RlyTo = 0x02
    DevLgnAck = 0x11
    P2PReqAck = 0x21
    ListenReqAck = 0x69
    RlyHelloAck = 0x70  # always
    RlyHelloAck2 = 0x71  # if len >1??


class JsonCommands(Enum):
    CMD_SET_CYPUSH = 1
    CMD_CHECK_USER = 100
    CMD_GET_PARMS = 101
    CMD_DEV_CONTROL = 102
    CMD_EDIT_USER = 106
    CMD_GET_ALARM = 107
    CMD_SET_ALARM = 108
    CMD_STREAM = 111
    CMD_GET_WIFI = 112
    CMD_SCAN_WIFI = 113
    CMD_SET_WIFI = 114
    CMD_SET_DATETIME = 126  # returns result as cmd 128...
    CMD_PTZ_CONTROL = 128
    CMD_GET_RECORD_PARAM = 199
    CMD_TALK_SEND = 300
    CMD_SET_WHITELIGHT = 304
    CMD_GET_WHITELIGHT = 305
    CMD_GET_CLOUD_SUPPORT = 9000


class PTZ(Enum):
    # Pan-tilt-zoom control
    UP_START = 0
    UP_STOP = 1
    DOWN_START = 2
    DOWN_STOP = 3
    LEFT_START = 4
    LEFT_STOP = 5
    RIGHT_START = 6
    RIGHT_STOP = 7

    # TILT_UP_START = 0
    # TILT_UP_STOP = 1
    # TILT_DOWN_START = 2
    # TILT_DOWN_STOP = 3
    # PAN_LEFT_START = 4
    # PAN_LEFT_STOP = 5
    # PAN_RIGHT_START = 6
    # PAN_RIGHT_STOP = 7


JSON_COMMAND_NAMES = {
    JsonCommands.CMD_SET_CYPUSH: "set_cypush",
    JsonCommands.CMD_CHECK_USER: "check_user",
    JsonCommands.CMD_GET_PARMS: "get_parms",
    JsonCommands.CMD_DEV_CONTROL: "dev_control",
    JsonCommands.CMD_EDIT_USER: "edit_user",
    JsonCommands.CMD_GET_ALARM: "get_alarm",
    JsonCommands.CMD_SET_ALARM: "set_alarm",
    JsonCommands.CMD_STREAM: "stream",
    JsonCommands.CMD_GET_WIFI: "get_wifi",
    JsonCommands.CMD_SCAN_WIFI: "scan_wifi",
    JsonCommands.CMD_SET_WIFI: "set_wifi",
    JsonCommands.CMD_SET_DATETIME: "set_datetime",
    JsonCommands.CMD_PTZ_CONTROL: "ptz_control",
    JsonCommands.CMD_GET_RECORD_PARAM: "get_record_param",
    JsonCommands.CMD_TALK_SEND: "talk_send",
    JsonCommands.CMD_SET_WHITELIGHT: "set_whiteLight",
    JsonCommands.CMD_GET_WHITELIGHT: "get_whiteLight",
    JsonCommands.CMD_GET_CLOUD_SUPPORT: "get_cloudsupport",
}
