from enum import Enum, IntEnum, IntFlag

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


class DevCfgId(IntEnum):
    """Config-section identifiers used by the DFTCFG import/export commands.

    These are section selectors carried inside a config command's payload, not
    command opcodes, and they reuse the 0x0000-0x0019 range that the ACK status
    codes also occupy. Keeping them in their own enum prevents value aliasing
    that would make BinaryCommands(value) ambiguous.
    """
    CFGID_VERSION = 0x0000
    CFGID_LANGUAGE = 0x0001
    CFGID_PRODUCTE = 0x0002
    CFGID_UPGRADE = 0x0003
    CFGID_P2P = 0x0004
    CFGID_TZ = 0x0005
    CFGID_USER = 0x0006
    CFGID_OPR = 0x0007
    CFGID_SERIAL = 0x0008
    CFGID_WIRED = 0x0009
    CFGID_WLAN = 0x000A
    CFGID_OSD = 0x000B
    CFGID_IMG = 0x000C
    CFGID_CMOS = 0x000D
    CFGID_PTZ = 0x000E
    CFGID_AUDIO = 0x000F
    CFGID_VIDEO = 0x0010
    CFGID_RECPOLICY = 0x0011
    CFGID_RESCH = 0x0012
    CFGID_MDALARM = 0x0013
    CFGID_ADCALARM = 0x0014
    CFGID_INPUTALARM = 0x0015
    CFGID_SMTP = 0x0016
    CFGID_FTP = 0x0017
    CFGID_PUSH = 0x0018
    CFGID_WLANPMK = 0x0019


class AckCode(IntEnum):
    """Result/status codes returned in an ACK payload (not command opcodes)."""
    CMD_ACK_OK = 0x0000
    CMD_ACK_UNAUTH = 0x0001
    CMD_ACK_NO_PRIVILEGE = 0x0002
    CMD_ACK_INVALID_PARAM = 0x0003
    CMD_ACK_CMDEXCUTE_FAILED = 0x0004
    CMD_ACK_NONE_RESULT = 0x0005
    CMD_ACK_UNKNOWN = 0x0006
    CMD_ACK_ILLIGAL = 0x03E8


class BinaryCommands(Enum):
    # Protocol-level markers: a DRW command frame is tagged BINCMD (255) or
    # CGICMD (254) to select the binary vs the CGI command vocabulary.
    CGICMD = 0x00FE
    BINCMD = 0x00FF
    CMD_DEV_BROADCAST = 0x0EFF
    CMD_SYSTEM_DFTCFG_IMPORT = 0x1000
    CMD_SYSTEM_DFTCFG_EXPORT = 0x1001
    CMD_SYSTEM_DFTCFG_RECOVERY = 0x1002
    CMD_SYSTEM_ITEMDFTCFG_RECOVERY = 0x1003
    CMD_SYSTEM_CRNCFG_EXPORT = 0x1004
    CMD_SYSTEM_CRNCFG_IMPORT = 0x1005
    CMD_SYSTEM_DFTCFG_CREATE = 0x1006
    CMD_SYSTEM_UPGRAD_SET = 0x1007
    CMD_SYSTEM_STATUS_GET = 0x1008
    CMD_SYSTEM_UPGRAD_GET = 0x1009
    CMD_SYSTEM_SHUTDOWN = 0x1010
    CMD_SYSTEM_REBOOT = 0x1011
    CMD_SYSTEM_INF_GET = 0x1012
    CMD_SYSTEM_ALIAS_SET = 0x1013
    CMD_SYSTEM_USER_CHK = 0x1020
    CMD_SYSTEM_USER_SET = 0x1021
    CMD_SYSTEM_USER_GET = 0x1022
    CMD_SYSTEM_USER_CHG = 0x1023
    CMD_SYSTEM_P2PPARAM_SET = 0x1030
    CMD_SYSTEM_OPRPOLICY_SET = 0x1031
    CMD_SYSTEM_OPRPOLICY_GET = 0x1032
    CMD_SYSTEM_P2PPARAM_GET = 0x1033
    CMD_SYSTEM_DATETIME_SET = 0x1040
    CMD_SYSTEM_DATETIME_GET = 0x1041
    CMD_NOTIFICATION = 0x1051
    ACK_SYSTEM_DFTCFG_IMPORT = 0x1100
    ACK_SYSTEM_DFTCFG_EXPORT = 0x1101
    ACK_SYSTEM_DFTCFG_RECOVERY = 0x1102
    ACK_SYSTEM_ITEMDFTCFG_RECOVERY = 0x1103
    ACK_SYSTEM_CRNCFG_EXPORT = 0x1104
    ACK_SYSTEM_CRNCFG_IMPORT = 0x1105
    ACK_SYSTEM_DFTCFG_CREATE = 0x1106
    ACK_SYSTEM_UPGRAD_SET = 0x1107
    ACK_SYSTEM_STATUS_GET = 0x1108
    ACK_SYSTEM_UPGRAD_GET = 0x1109
    ACK_SYSTEM_SHUTDOWN = 0x1110
    ACK_SYSTEM_REBOOT = 0x1111
    ACK_SYSTEM_INF_GET = 0x1112
    ACK_SYSTEM_ALIAS_SET = 0x1113
    ACK_SYSTEM_USER_CHK = 0x1120
    ACK_SYSTEM_USER_SET = 0x1121
    ACK_SYSTEM_USER_GET = 0x1122
    ACK_SYSTEM_USER_CHG = 0x1123
    ACK_SYSTEM_P2PPARAM_SET = 0x1130
    ACK_SYSTEM_OPRPOLICY_SET = 0x1131
    ACK_SYSTEM_OPRPOLICY_GET = 0x1132
    ACK_SYSTEM_P2PPARAM_GET = 0x1133
    ACK_SYSTEM_DATETIME_SET = 0x1140
    ACK_SYSTEM_DATETIME_GET = 0x1141
    ACK_NOTIFICATION = 0x1151
    CMD_SD_FORMAT = 0x2000
    CMD_SD_RECPOLICY_SET = 0x2001
    CMD_SD_RECPOLICY_GET = 0x2002
    CMD_SD_RECORDING_NOW = 0x2003
    CMD_SD_INFO_GET = 0x2004
    CMD_SD_RECORDFILE_GET = 0x2005
    CMD_SD_RECORDSCH_GET = 0x2006
    CMD_SD_RECORDSCH_SET = 0x2007
    CMD_SD_RETRIVEL = 0x2008
    CMD_SD_PICFILE_GET = 0x2009
    CMD_SD_PIC_CAPTURE = 0x200A
    CMD_SD_REC_DEL = 0x200B
    CMD_SD_PIC_DEL = 0x200C
    CMD_SD_SPL_DEL = 0x200D
    ACK_SD_FORMAT = 0x2100
    ACK_SD_RECPOLICY_SET = 0x2101
    ACK_SD_RECPOLICY_GET = 0x2102
    ACK_SD_RECORDING_NOW = 0x2103
    ACK_SD_INFO_GET = 0x2104
    ACK_SD_RECORDFILE_GET = 0x2105
    ACK_SD_RECORDSCH_GET = 0x2106
    ACK_SD_RECORDSCH_SET = 0x2107
    ACK_SD_RETRIVEL = 0x2108
    ACK_SD_PICFILE_GET = 0x2109
    ACK_SD_PIC_CAPTURE = 0x210A
    ACK_SD_REC_DEL = 0x210B
    ACK_SD_PIC_DEL = 0x210C
    ACK_SD_SPL_DEL = 0x210D
    CMD_PEER_LIVEAUDIO_START = 0x3000
    CMD_PEER_LIVEAUDIO_STOP = 0x3001
    CMD_LOCAL_LIVEAUDIO_START = 0x3002
    CMD_LOCAL_LIVEAUDIO_STOP = 0x3003
    CMD_PEER_AUDIOPARAM_SET = 0x3004
    CMD_PEER_AUDIOPARAM_GET = 0x3005
    CMD_PEER_AUDIOFILE_STARTPLAY = 0x3006
    CMD_PEER_AUDIOFILE_STOPPLAY = 0x3007
    CMD_PEER_AUDIOFILELIST_GET = 0x3008
    CMD_PEER_IRCUT_ONOFF = 0x300A
    CMD_PEER_LIGHTFILL_ONOFF = 0x300B
    CMD_PEER_LIVEVIDEO_START = 0x3010
    CMD_PEER_LIVEVIDEO_STOP = 0x3011
    CMD_PEER_PLAYBACK_START = 0x3012
    CMD_PEER_PLAYBACK_STOP = 0x3013
    CMD_PEER_PLAYBACK_SEEK = 0x3014
    CMD_PEER_PLAYBACK_SPEED = 0x3015
    CMD_PEER_PLAYBACK_PAUSE = 0x3016
    CMD_PEER_PLAYBACK_RESUME = 0x3017
    CMD_PEER_VIDEOPARAM_SET = 0x3018
    CMD_PEER_VIDEOPARAM_GET = 0x3019
    CMD_SNAPSHOT_GET = 0x301A
    CMD_PEER_PLAYBACK_END = 0x301B
    CMD_PEER_PLAYBACK_STEP = 0x301C
    CMD_DOORBELL_CALL_OPEN = 0x3020
    CMD_DOORBELL_CALL_CLOSE = 0x3021
    CMD_DOORBELL_CALL_ACCEPT = 0x3022
    CMD_DOORBELL_CALL_REJECT = 0x3023
    CMD_LOCAL_LIVEVIDIO_SEND_ON = 0x3024
    CMD_LOCAL_LIVEVIDIO_SEND_OFF = 0x3025
    CMD_LOCAL_AUDIO_STATUS_SET = 0x3026
    CMD_LOCAL_AUDIO_STATUS_GET = 0x3027
    CMD_LOCAL_AVREC_START = 0x3028
    CMD_LOCAL_AVREC_STOP = 0x3029
    CMD_LOCAL_PLAYBACK_START = 0x3030
    CMD_LOCAL_PLAYBACK_STOP = 0x3031
    CMD_LOCAL_PLAYBACK_SEEK = 0x3032
    CMD_LOCAL_PLAYBACK_PAUSE = 0x3033
    CMD_LOCAL_PLAYBACK_RESUME = 0x3034
    CMD_LOCAL_PLAYBACK_START1 = 0x3035
    CMD_LOCAL_PLAYBACK_STEP = 0x3036
    CMD_LOCAL_PLAYBACK_START2 = 0x3037
    CMD_LOCAL_MJREC_START = 0x3040
    CMD_LOCAL_MJREC_STOP = 0x3041
    ACK_PEER_LIVEAUDIO_START = 0x3100
    ACK_PEER_LIVEAUDIO_STOP = 0x3101
    ACK_LOCAL_LIVEAUDIO_START = 0x3102
    ACK_LOCAL_LIVEAUDIO_STOP = 0x3103
    ACK_PEER_AUDIOPARAM_SET = 0x3104
    ACK_PEER_AUDIOPARAM_GET = 0x3105
    ACK_PEER_AUDIOFILE_STARTPLAY = 0x3106
    ACK_PEER_AUDIOFILE_STOPPLAY = 0x3107
    ACK_PEER_AUDIOFILELIST_GET = 0x3108
    ACK_PEER_IRCUT_ONOFF = 0x310A
    ACK_PEER_LIGHTFILL_ONOFF = 0x310B
    ACK_PEER_LIVEVIDEO_START = 0x3110
    ACK_PEER_LIVEVIDEO_STOP = 0x3111
    ACK_PEER_PLAYBACK_START = 0x3112
    ACK_PEER_PLAYBACK_STOP = 0x3113
    ACK_PEER_PLAYBACK_SEEK = 0x3114
    ACK_PEER_PLAYBACK_SPEED = 0x3115
    ACK_PEER_PLAYBACK_PAUSE = 0x3116
    ACK_PEER_PLAYBACK_RESUME = 0x3117
    ACK_PEER_VIDEOPARAM_SET = 0x3118
    ACK_PEER_VIDEOPARAM_GET = 0x3119
    ACK_SNAPSHOT_GET = 0x311A
    ACK_PEER_PLAYBACK_END = 0x311B
    ACK_PEER_PLAYBACK_STEP = 0x311C
    CMD_FILE_CTRL = 0x4000
    CMD_FILETRANSFER_FILELIST_GET = 0x4005
    CMD_LOCALPATH = 0x4010
    ACK_FILE_CREATE = 0x4101
    ACK_FILE_RENAME = 0x4102
    ACK_FILE_DELETE = 0x4103
    ACK_FILE_MOVE = 0x4104
    ACK_FILE_LIST = 0x4105
    ACK_FILE_DOWNLOAD = 0x4110
    ACK_FILE_DOWNLOAD_PAUSE = 0x4111
    ACK_FILE_DOWNLOAD_RESUME = 0x4112
    ACK_FILE_DOWNLOAD_CANCEL = 0x4113
    ACK_FILE_UPLOAD = 0x4120
    ACK_FILE_UPLOAD_PAUSE = 0x4121
    ACK_FILE_UPLOAD_RESUME = 0x4122
    ACK_FILE_UPLOAD_CANCEL = 0x4123
    CMD_PTZ_SET = 0x5049
    ACK_PTZ_SET = 0x5149
    CMD_PTZ_GET = 0x5050
    CMD_PASSTHROUGH_STRING_PUT = 0x50FF
    ACK_PASSTHROUGH_STRING_PUT = 0x51FF
    CMD_SESSION_CHECK = 0x55FE
    CMD_NET_WIFISETTING_SET = 0x6001
    CMD_NET_WIFISETTING_GET = 0x6002
    CMD_NET_WIFI_SCAN = 0x6003
    CMD_NET_WIREDSETTING_SET = 0x6004
    CMD_NET_WIREDSETTING_GET = 0x6005
    ACK_NET_WIFISETTING_SET = 0x6101
    ACK_NET_WIFISETTING_GET = 0x6102
    ACK_NET_WIFI_SCAN = 0x6103
    ACK_NET_WIREDSETTING_SET = 0x6104
    ACK_NET_WIREDSETTING_GET = 0x6105
    CMD_FRIEND_MSG = 0x7000
    CMD_LOCAL_SESSION_INF = 0xF000
    CMD_LOCAL_SESSION_CHECK = 0xF001
    CMD_LOCAL_SESSION_GET = 0xF002
    CMD_LOCAL_SESSION_CTRL = 0xF003
    CMD_LOCAL_REC_START = 0xF004
    CMD_LOCAL_REC_STOP = 0xF005
    CMD_LOCAL_REC_MERGECTRL = 0xF006
    CMD_LOCAL_P2P_START = 0xF007
    CMD_LOCAL_P2P_STOP = 0xF008
    CMD_SESSION_CLOSE = 0xF00F
    CMD_LOCAL_PUSH_STRING = 0xF010
    CMD_LOCAL_PUSH_CFG = 0xF011
    CMD_LOCAL_RCVVID_DEC = 0xF012
    CMD_LOCAL_LAPSED = 0xF021


class CgiCommands(IntEnum):
    """The alternate CGI command vocabulary (selected by the CGICMD marker).

    Some A9/XD firmwares answer on these CB_* opcodes instead of the
    BinaryCommands (BINCMD) set. They occupy an overlapping numeric range --
    e.g. 0x6001-0x6005 collide with CMD_NET_* -- so they must live in their own
    enum to keep value-based lookups unambiguous.
    """
    CB_IEGET_STATUS = 0x6001
    CB_IEGET_PARAM = 0x6002
    CB_IEGET_CAM_PARAMS = 0x6003
    CB_IEGET_LOG = 0x6004
    CB_IEGET_MISC = 0x6005
    CB_IEGET_RECORD = 0x6006
    CB_IEGET_RECORD_FILE = 0x6007
    CB_IEGET_WIFI_SCAN = 0x6008
    CB_IEGET_FACTORY = 0x6009
    CB_IESET_IR = 0x600A
    CB_IESET_UPNP = 0x600B
    CB_IESET_ALARM = 0x600C
    CB_IESET_LOG = 0x600D
    CB_IESET_USER = 0x600E
    CB_IESET_ALIAS = 0x600F
    CB_IESET_MAIL = 0x6010
    CB_IESET_WIFI = 0x6011
    CB_CAM_CONTROL = 0x6012
    CB_IESET_DATE = 0x6013
    CB_IESET_MEDIA = 0x6014
    CB_IESET_SNAPSHOT = 0x6015
    CB_IESET_DDNS = 0x6016
    CB_IESET_MISC = 0x6017
    CB_IEGET_FTPTEST = 0x6018
    CB_DECODER_CONTROL = 0x6019
    CB_IESET_DEFAULT = 0x601A
    CB_IESET_MOTO = 0x601B
    CB_IEGET_MAILTEST = 0x601C
    CB_IESET_MAILTEST = 0x601D
    CB_IEDEL_FILE = 0x601E
    CB_IELOGIN = 0x601F
    CB_IESET_DEVICE = 0x6020
    CB_IESET_NETWORK = 0x6021
    CB_IESET_FTPTEST = 0x6022
    CB_IESET_DNS = 0x6023
    CB_IESET_OSD = 0x6024
    CB_IESET_FACTORY = 0x6025
    CB_IESET_PPPOE = 0x6026
    CB_IEREBOOT = 0x6027
    CB_IEFORMATSD = 0x6028
    CB_IESET_RECORDSCH = 0x6029
    CB_IESET_WIFISCAN = 0x602A
    CB_IERESTORE = 0x602B
    CB_IESET_FTP = 0x602C
    CB_IESET_RTSP = 0x602D
    CB_IEGET_VIDEOSTREAM = 0x602E
    CB_UPGRADE_APP = 0x602F
    CB_UPGRADE_SYS = 0x6030
    CB_SET_IIC = 0x6031
    CB_GET_IIC = 0x6032
    CB_IEGET_ALARMLOG = 0x6033
    CB_IESET_ALARMLOGCLR = 0x6034
    CB_IEGET_SYSWIFI = 0x6035
    CB_IESET_SYSWIFI = 0x6036
    CB_IEGET_LIVESTREAM = 0x6037
    CB_NOTIFICATION = 0x6040
    CB_IEGET_BILL = 0x6053
    CB_APP_VERSION = 0x6054
    CB_CHECK_USER = 0x60A0
    CB_IESET_BILL = 0x60A1
    CB_SET_P2PPARAM = 0x99F0
    CB_GET_SYSOPR = 0x99FE
    CB_SET_SYSOPR = 0x99FF
    CB_SET_SINGLE_SETTING_DEFAULT = 0xFF01
    CB_GET_FILE = 0xFF10
    CB_PUT_FILE = 0xFF11
    CB_SET_FILE = 0xFF12
    CB_GET_FILELIST = 0xFF13
    CB_SET_GPIO = 0xFF14
    CB_GET_GPIO = 0xFF15
    CB_GET_ADC = 0xFF16


CC_DEST = {
    BinaryCommands.CMD_SYSTEM_USER_CHK: 0x00ff,
    BinaryCommands.CMD_SYSTEM_STATUS_GET: 0x0000,
    BinaryCommands.CMD_PEER_LIVEVIDEO_START: 0x0000,
    BinaryCommands.CMD_PEER_LIVEVIDEO_STOP: 0x0000,  # ????
    BinaryCommands.CMD_NET_WIFI_SCAN: 0x0000,
    BinaryCommands.CMD_NET_WIFISETTING_GET: 0x0000,
    BinaryCommands.CMD_PASSTHROUGH_STRING_PUT: 0x0000,

    BinaryCommands.ACK_NET_WIFI_SCAN: 0x55aa,
    BinaryCommands.ACK_SYSTEM_USER_CHK: 0x00ff,  # 0x55aa,
    BinaryCommands.ACK_SYSTEM_STATUS_GET: 0x55aa,
}


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

class PtzDirection(IntEnum):
    PTZ_DIRECTION_UP = 0
    PTZ_DIRECTION_DOWN = 1
    PTZ_DIRECTION_LEFT = 2
    PTZ_DIRECTION_RIGHT = 3

    PTZ_DIRECTION_UP_DOWN = 4
    PTZ_DIRECTION_LEFT_RIGHT = 5
    PTZ_DIRECTION_LEFT_UP = 6
    PTZ_DIRECTION_LEFT_DOWN = 7
    PTZ_DIRECTION_RIGHT_UP = 8
    PTZ_DIRECTION_RIGHT_DOWN = 9

    PTZ_DIRECTION_CENTER = 10

    PTZ_DIRECTION_ORIGINAL = 11
    PTZ_DIRECTION_STOP = 12

    PTZ_DIRECTION_PRE_TO = 13
    PTZ_DIRECTION_PRE_REC = 14

    PTZ_DIRECTION_SPEED = 15

class PtzParamType(IntEnum):
    PTZ_PARAM_TYPE_DIRECTION = 0
    PTZ_PARAM_TYPE_PREFAB = 1
    PTZ_PARAM_TYPE_CRNTPOS = 2

class PtzPrefab(IntEnum):
    PTZ_PREFAB_SET = 0
    PTZ_PREFAB_GET = 1
    PTZ_PREFAB_REC = 2
    PTZ_PREFAB_CHK = 3
    PTZ_PREFAB_DEL = 4
    PTZ_PREFAB_INT = 5

# Seems that only Resolution and Bitrate are supported
class VideoParamType(IntEnum):
    VIDEO_PARAM_TYPE_DEFAULT = 0
    VIDEO_PARAM_TYPE_RESOLUTION = 1
    VIDEO_PARAM_TYPE_BRIGHTNESS = 2
    VIDEO_PARAM_TYPE_CONTRAST = 3
    VIDEO_PARAM_TYPE_SATURATION = 4
    VIDEO_PARAM_TYPE_SHARPNESS = 5
    VIDEO_PARAM_TYPE_FRAMERATE = 6
    VIDEO_PARAM_TYPE_BITRATE = 7
    VIDEO_PARAM_TYPE_ROTATE = 8
    VIDEO_PARAM_TYPE_IRCUT = 9
    VIDEO_PARAM_TYPE_OSD = 10
    VIDEO_PARAM_TYPE_MOVEDETECTION = 11
    VIDEO_PARAM_TYPE_MODE = 12
    VIDEO_PARAM_TYPE_SCENE_NORMAL = 90
    VIDEO_PARAM_TYPE_SCENE_HUMAN = 91
    VIDEO_PARAM_TYPE_SCENE_COOL = 92
    VIDEO_PARAM_TYPE_SCENE_BLACKLIGHT = 93

# HD and up may not be supported on most devices
class VideoResolution(IntEnum):
    VIDEO_RESOLUTION_QVGA = 0
    VIDEO_RESOLUTION_VGA = 1
    VIDEO_RESOLUTION_HD = 2
    VIDEO_RESOLUTION_FD = 3
    VIDEO_RESOLUTION_UD = 4

class VideoRotate(IntEnum):
    VIDEO_ROTATE_NORMAL = 0
    VIDEO_ROTATE_H = 1
    VIDEO_ROTATE_V = 2
    VIDEO_ROTATE_HV = 3


# --------------------------------------------------------------------------
# Model/capability enums lifted from the vendor apps' own constant classes.
# They name a raw value; they do not change how anything is decoded, and an
# unrecognised value yields None rather than a wrong name. Background and
# per-value evidence: VENDOR_APP_FINDINGS.md.
# --------------------------------------------------------------------------

class DevType(IntEnum):
    """`(swVer >> 8) & 0xff`. The app derives this from the firmware version,
    falling back to the DID prefix (FTZ/PTZ/PIZ -> BK_PTZ, else BK_A9) before
    the first status reply arrives.

    Both vendor apps ship their own `Constants.DevType` and they DISAGREE.
    Names below follow FtyCamPro 1.117, which is the superset -- it adds 42,
    62 and 95, which YsxLite 1.40 lacks. Where the two differ:

        id  FtyCamPro 1.117     YsxLite 1.40
        0   DEV_BK_UNKNOWN      DEV_MYSELF
        6   DEV_BK_A9_EXT       DEV_BK_A9_PWRSWITCH_NOSD
        8   DEV_BK_A9_XD18      DEV_BK_A9_NOCARD1
        9   DEV_BK_A9_PLUS      DEV_BK_UMS

    Ids 1-5, 7, 10-12, 14-16, 20, 25, 35, 55 and 65 are identical in both.

    Treat the capability-sounding suffixes with care: only YsxLite claims 6 is
    "PWRSWITCH_NOSD" and 8 is "NOCARD1". What IS corroborated by behaviour is
    that FtyCamPro hides the battery icon for devType 6, so "externally
    powered" fits either name.

    Confirmed on hardware: 2 = BK_A9 on a fixed FTYC camera, 15 = XR_PTZ on a
    pan/tilt PTZA camera.
    """
    DEV_BK_UNKNOWN = 0
    DEV_BK_A9_NOCARD = 1
    DEV_BK_A9 = 2
    DEV_BK_XD15 = 3
    DEV_BK_USB = 4
    DEV_BK_PTZ = 5
    DEV_BK_A9_EXT = 6
    DEV_BK_DCAR = 7
    DEV_BK_A9_XD18 = 8
    DEV_BK_A9_PLUS = 9
    DEV_BK_A9_PWRSWITCH_SD = 10
    DEV_BK_A9_CGZ = 11
    DEV_BK_PIZ_BULIT = 12
    DEV_XR_A9 = 14
    DEV_XR_PTZ = 15
    DEV_TX_A9 = 16
    DEV_BK_LPWR_LPS = 20
    DEV_BTPTZ_DCAM_BK = 25
    DEV_BTPTZ_DCAM_TX = 35
    DEV_TX_8076H19 = 42
    DEV_TX_PTZ = 55
    DEV_TX817_DMINI = 62
    DEV_BKPTZ_DCAM = 65
    DEV_TX817_YSX_W15 = 95


class ChipType(IntEnum):
    """`(swVer >> 24) & 0xff`, from FtyCamPro's `Constants.ChpType`.

    Single-sourced: YsxLite 1.40 has no ChpType class and no
    getChpTypeFromDevVer at all, so there is nothing to cross-check against
    and this list is certainly incomplete -- it names only the TX family.

    FTYC hardware reports 61 (TX_817_810). PTZA reports 2, which is outside
    this set and so decodes to None. Note the chip family does NOT track the
    DevType prefix: FTYC is DEV_BK_A9, a "BK" name, on a TX chip.
    """
    CHP_UNKNOWN = 0
    CHP_TX = 60
    CHP_TX_817_810 = 61
    CHP_TX_817_H24 = 62
    CHP_TX_818_C01 = 63


class DevSysMode(IntEnum):
    """`(powerSupply >> 4) & 0x0f`. App labels: "On configuring" / "Normal" /
    "Low power" (resources dev_sysmode_cfg/nml/lpr)."""
    SYSMODE_QRCFG = 0
    SYSMODE_NORMAL = 1
    SYSMODE_LOWPWR = 2


class DevFunc(IntFlag):
    """`(powerSupply >> 24) & 0xff` -- live state bitmap.

    Bits 0 and 1 are CONFIRMED on FTYC hardware (2026-08-25): toggling IR
    moved the byte 0x14 -> 0x16 (bit 1) and the light button 0x16 -> 0x17
    (bit 0), with nothing else in the 124-byte block changing. That also
    settles the ambiguity between the two vendor activities in favour of
    LiveDoubleAty -- LiveSingleAty's "bit 0 = OSD" really was a copy-paste
    slip. Note the light bit follows the *commanded* state: the test camera
    has no white LED at all and bit 0 still set.

    Bit 2 is NOT confirmed. LiveSingleAty maps it to OSD, but FTYC reports it
    permanently set while the status block's own osdEnable reads 0, so one of
    the two is not OSD. Bit 4 is likewise permanently set and unexplained.
    """
    FILL_LIGHT = 0x01     # confirmed
    IR_LED = 0x02         # confirmed
    OSD = 0x04            # per LiveSingleAty; contradicted by FTYC's osdEnable
    BIT3 = 0x08
    BIT4 = 0x10           # always set on FTYC, meaning unknown
    BIT5 = 0x20
    BIT6 = 0x40
    BIT7 = 0x80


class SDCardStatus(IntEnum):
    UNEXIST = 0
    UNFORMAT = 1
    BAD_FORMAT = 2
    DIRTY = 3
    OK = 4
    INITIALIZE_FAILED = 5
    NOT_INITIALIZED = 6
    RECORDING = 7
    RECSTOP = 8
    RECFAILED = 9
    RW_FAILED = 10
    TIME_PROBLEM = 11
    SPACE_INSUFFICIENT = 12
    OPR_NORESULT = 13
    OPR_COLLISION = 14
    FORMATTING = 15
    RETRYING = 16


class WifiMode(IntEnum):
    """The app's own picker only ever offers these two, and treats mode 0 as
    station. PTZA reports 1 while joined to an AP, which is outside this set --
    so either that firmware encodes the field differently or our offset is
    wrong for it. Unknown values decode to None rather than being guessed."""
    INFRA = 0
    AP = 2


class WifiType(IntEnum):
    """Wi-Fi auth type. These labels are the vendor app's own literals
    (SetWifiAty.getTypeName), so they are the one enum here that is directly
    attested rather than transcribed."""
    NONE = 0
    WEP_OPEN = 1
    WEP_SHARED = 2
    WPA = 3
    WPA2 = 4


class LibError(IntEnum):
    """Result code returned in the 4-byte *token* field of a binary command
    reply (see `parse_drw_pkt`) -- not in the payload. Negative means refused,
    and such a reply carries no payload at all.

    Transcribed verbatim from YsxLite 1.40's `LibError`, misspellings included
    (CMD_EXCUTE_FAILED, INSUFICIENT_RESOURCE, LOCAL_PEMISSION, UNKOWN,
    FILE_MAX_NMB), so the names stay greppable against the decompiled app.

    Observed on PTZA fw 2.2.15.93 without a login:
        CMD_SYSTEM_USER_CHK -> -1010 CMD_EXCUTE_FAILED (bad credentials)
        CMD_SYSTEM_REBOOT   -> -1015 USER_NO_PRIVILEGE
        CMD_NET_WIFI_SCAN   -> -1010 CMD_EXCUTE_FAILED
    """
    OK = 0
    NOT_READY = 1
    ERROR_IPC_BASE = -1000
    NOT_INITIALIZED = -1001
    ALREADY_INITIALIZED = -1002
    NOT_STARTED = -1003
    ALREADY_STARTED = -1004
    INVALID_PARAMETER = -1005
    INSUFICIENT_RESOURCE = -1006
    LOCAL_MAX_SESSION = -1007
    PEER_MAX_SESSION = -1008
    ILLEGAL_CMD = -1009
    CMD_EXCUTE_FAILED = -1010
    CMD_PARSE = -1011
    USER_ACC_NONEXIST = -1012
    USER_PWD_INCORRECT = -1013
    UNAUTH = -1014
    USER_NO_PRIVILEGE = -1015
    INVALID_SESSION = -1016
    FILE_NOEXIST = -1020
    FILE_OPEN_FAILED = -1021
    FILE_MAX_NMB = -1022
    LOCAL_PEMISSION = -1998
    UNKOWN = -1999


def enum_name(enum_cls, value, prefix=''):
    """Name for a raw enum value, or None when it isn't in the enum.

    Used to annotate device info without ever hiding the raw number -- several
    of these enums are incomplete, so an unknown value must stay visible rather
    than being coerced into the nearest name.
    """
    if value is None:
        return None
    try:
        name = enum_cls(value).name
    except ValueError:
        return None
    return name[len(prefix):] if prefix and name.startswith(prefix) else name