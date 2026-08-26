# aiopppp

**aiopppp** is an asynchronous Python library designed to simplify connecting to and interacting with cameras that
utilize the Peer-to-Peer Protocol (PPPP) which is implemented in some cheap cameras (A9, X5, etc.)
This library enables seamless communication with compatible cameras for live video streaming,
audio, capturing snapshots, or configuring camera settings, all using asyncio for efficient performance.

## Features

- Camera discovery: both `LanSearch` and `LanSearchExt` probes, plain and
  encoded (not all keys) — some firmwares only answer the extended variant
- Asynchronous peer-to-peer connections with PPPP-enabled cameras using both JSON and binary control protocols
- Live MJPEG video streaming, including cameras that mux audio into the video channel (FTYC)
- **Two-way audio** on binary cameras: G.711 (A-law/µ-law) listening and talk-back to the camera speaker
- PTZ movement (up/down/left/right, step and continuous)
- White light / IR light control, image flip/mirror (camera-dependent)
- Video parameters: resolution, bitrate, etc. — set and read-back
- Snapshots (via `CMD_SNAPSHOT_GET` where supported, otherwise from the live video frame buffer)
- System commands: reboot, device status (battery, power source, Wi-Fi signal), date/time sync
  (both known firmware layouts), device info, Wi-Fi settings read-out
- Automatic reconnection with backoff (`Device` high-level API)
- SD card listing and playback control *(implemented, untested on hardware)*
- Test web server with a per-camera control page, plus a protocol simulator and a
  transparent DID-rewriting proxy for debugging
- Lightweight: Python 3.7+, depends only on `aiohttp`

## Tested Devices

| Prefix   | Protocol | Video | Audio (listen) | Talk | PTZ | White Light | IR Light | Reboot | Resolution | Flip/Mirror | Time sync |
|:---------|:---------|:-----:|:--------------:|:----:|:---:|:-----------:|:--------:|:------:|:----------:|:-----------:|:---------:|
| **DGOK** | 📜 JSON  | ✅   | ✖️            | ✖️  | ✅  | ✅          | ✅      | ✅     | ✖️        | ❔          | ✖️       |
| **PTZA** | 🔢 Binary| ✅   | ✅            | ✅  | ✅  | ✅          | 🚫      | ✅     | ✅        | 🚫          | ✅       |
| **FTYC** | 🔢 Binary| ✅   | ✅            | 🚫  | 🚫  | 🚫          | ✅      | ✅     | ✅        | ✅          | ⚠️       |
| [**BATE**<sup>*</sup>](https://github.com/devbis/pppp_camera/issues/4) | 🔢 Binary|❔ |❔ | ❔ | ❔   | ❔           | ❔       | ❔     |  ❔        | ❔          | ❔       |
| [**DGB**<sup>*</sup>](https://github.com/devbis/pppp_camera/issues/2) | 📜 JSON   |⚠️ |✖️ | ✖️ | ❔   | ❔           | ❔       | ❔     |  ❔        | ❔          | ✖️       |
| [**ACCQ**<sup>*</sup>](https://github.com/devbis/pppp_camera/issues/1) | ❔ Unknown|✖️|✖️ | ✖️ | ✖️  | ✖️          | ✖️      | ✖️     | ✖️        | ✖️          | ✖️       |

**Legend:**
- &nbsp;✅&nbsp; **Working**: Feature is fully functional.
- &thinsp;⚠️&thinsp;**Partially working**: Feature works with limitations or issues.
- &nbsp;❌&nbsp; **Not working**: Feature is implemented but does not function.
- &nbsp;✖️&nbsp; **Not implemented**: Feature is not implemented in the system.
- &nbsp;🚫&nbsp; **Not supported**: Feature is not supported by the device.
- &ensp;❔ &nbsp; **Not tested**: Feature has not been tested on the device.

Notes: FTYC has no speaker, hence no talk-back. Time sync sets the clock on
both binary firmwares (verified within a couple of seconds of the host), but
FTYC has no timezone field to write, so its offset stays whatever the vendor
app configured — hence ⚠️ rather than ✅. JSON cameras expose no set-time
command at all. Device alias is not supported
by the tested firmwares (the vendor app doesn't implement it either).
Flip/mirror (`rotate` video param) works on FTYC, is ACKed but ignored by
PTZA. PTZ presets are implemented with the PREFAB scheme found in YsxLite
(`ptz_set_preset` / `ptz_goto_preset` / `ptz_delete_preset` /
`ptz_query_presets`) but appear **not supported by the tested cameras** — the
camera does not act on them. Left in as best-effort in case other firmwares
honor it.

## Hardware-confirmed protocol notes

These were established against real PTZA/FTYC cameras and the decompiled
vendor apps, and are encoded in the library:

- **FTYC muxes audio into the video DRW channel.** Both share the
  `55 aa 15 a8` stream header; byte 4 is the stream type (`0x03` = JPEG frame
  header packet, `0x06` = audio). Video frames arrive as
  `[audio pkt][32-byte frame-header pkt][raw JPEG chunks…]`. The library
  demuxes automatically; FTYC audio flows whenever video streams.
- **Talk-back audio must be framed** with the same 32-byte stream header
  (type `0x06`, payload length at offset 16) — bare G.711 is ignored.
- **`VIDEOPARAM_GET` returns a table** of all params 1..12 (u32 each)
  regardless of the requested id; the value is at `table[param_id - 1]`.
- **Two DATETIME layouts** exist: PTZA stores `(UTC epoch, tz seconds west of
  UTC, ntp[64])`; FTYC has no tz field and stores a timestamp that renders as
  local time (it adds its own configured offset to the UTC epoch you set).
  `parse_datetime_block` auto-detects the layout; `set_datetime()` works on
  both (send UTC, preserve the NTP server).
- **Status block semantics** (per the vendor SDK parser): `batLevel` is the
  battery voltage in mV, and bit 0 of `powerSupply` is `externalPower`.
- **The field the SDK calls `sysUptime` is actually the Wi-Fi RSSI**, exposed
  as `dbm`. The name is the only thing about it that suggests a duration: in
  both vendor apps every read of it goes straight to `setWifidbm()` and is
  rendered by `wlanSigGet()`, whose buckets are RSSI ranges
  (−100/−85/−70/−55). There is no uptime display anywhere in either app.
  Values outside a plausible −127…−1 are reported as `None`. No `uptime` key
  is published.
- **`batPercent` is derived from a single-cell LiPo discharge curve**, not
  from the vendor thresholds. Those thresholds only choose one of five
  battery *icons* (≥4350/4200/4100/3950/3900 mV); read as percentages they
  pin a fully-charged camera resting at 4195 mV to "60%" indefinitely.
  `None` is returned when the field isn't a battery reading at all —
  mains-only cameras park it at 8000.
- **`CMD_SNAPSHOT_GET` is not answered** by any tested camera; use the video
  frame buffer for stills (the test web server does this automatically).

## Known issues

- **`start_video()` always requests HD, ignoring any resolution set
  beforehand.** `_request_video(1)` sends a hardcoded HD parameter and then
  re-asserts it ~5 s later, because the cameras self-downgrade and ignore the
  value set at stream start. The re-assert is what makes a resolution chosen
  *while streaming* stick — but it also means a resolution set while idle is
  discarded, and the stall-recovery path (`_request_video(1)` again after
  `VIDEO_REREQUEST_SEC` without frames) can revert a running stream to HD.

  The fix is a per-session preferred resolution that `set_resolution()`
  records and `_request_video()` prefers over the constant, falling back to
  today's behaviour when unset. Not implemented yet.

## Untested / experimental

- **SD card & playback** (`get_sd_info`, `list_recordings`, `playback_*`):
  implemented from the decompiled apps, not yet verified on hardware.
- **Wi-Fi scan / device users** (`scan_wifi`, `get_users`): return empty data
  on already-configured cameras; probably only answered in AP/setup mode.
- **`set_wifi` — do not use**: the write layout very likely doesn't match the
  (confirmed) 264-byte read layout and could mis-provision the camera.
- **CGI command vocabulary** (`send_cgi_command`): experimental hook for
  firmwares speaking the CB_* command set; untested.
- **JSON-protocol cameras** (DGOK): functional but none of the recent fixes
  were exercised against one.

## Installation

To install the library, run:

```bash
pip install aiopppp
```

## Requirements

- Python 3.7 or higher
- Compatible PPPP-enabled cameras
- Required dependencies (automatically installed with `pip`):
  - `asyncio`
  - `aiohttp`

## Quick Start

### Prerequisites

The camera must be connected to WiFi using its mobile app. On the first start the camera creates WiFi access
point with the name like `DGXX-XXXX` or a different name. And it should be used for configuring WiFi settings.
After it is connected to you network you can use its IP address to connect to it.

The camera should use UDP port 32108 for discovery.
There are cameras with the same form-factor with open port 20190 which is not supported.
It uses either a different protocol or a different encryption.

Only one client can talk to a camera at a time — close the vendor app before
connecting.

### Usage

Here’s an example of how to use the library:

Using high-level device:
```python
import asyncio
from aiopppp import Device

async def main():
    async with Device("192.168.1.2") as device:
        print("Connected to the device")
        print("Device info:", device.properties)
        await device.start_video()
        await asyncio.sleep(10)
        await device.stop_video()
    print("Disconnected from the device")

    # or

    device = Device("192.168.1.2")
    await device.connect()
    print("Device info:", device.properties)
    await device.close()


asyncio.run(main())

```

Or low-level session connections:

```python
import asyncio
from aiopppp import find_device
from aiopppp.device import make_session
from contextlib import suppress

async def main():
    device = await find_device("192.168.1.2", timeout=20)
    disconnected = asyncio.Event()
    session = make_session(device, on_device_lost=lambda lost_device: disconnected.set())
    session.start()
    await asyncio.wait([session.device_is_ready.wait(), session.main_task], return_when=asyncio.FIRST_COMPLETED)
    if session.main_task.done():
        await session.main_task
        return
    print("Connected to the device")
    print("Device info:", session.dev_properties)
    session.stop()
    with suppress(asyncio.CancelledError):
        await session.main_task
    print("Disconnected from the device")



asyncio.run(main())
```

Or create discovery class and process found devices manually:
```python
import asyncio
from aiopppp import Discovery, JsonSession

def on_disconnect():
    print("Disconnected from the device")

def on_device_found(device):
    print(f"Found device: {device}")
    session = JsonSession(device, on_disconnect=on_disconnect)
    session.start()

async def main():
    discovery = Discovery(remote_addr='255.255.255.255')
    await discovery.discover(on_device_found)


asyncio.run(main())
```

## Running the test web server

The bundled web server discovers cameras and gives each one a full control page.

```bash
python -m aiopppp -u admin -p admin       # binary cameras (PTZA/FTYC default creds)
python -m aiopppp -u admin -p 6666        # JSON cameras
python -m aiopppp -a 192.168.1.255        # directed broadcast for your camera LAN
```

Visit `http://localhost:4000` — the index lists discovered cameras; each links
to `/camera/{dev_id}` with:

- live MJPEG stream, start/stop, snapshot button
- PTZ arrows (+ preset buttons — unsupported by tested firmwares)
- white light / IR buttons
- video parameters with read-back (current values pre-select the dropdowns;
  raw payload in the tooltip)
- audio: low-latency listen (Web Audio, ~0.1–0.5 s behind live), a buffered
  `<audio>` fallback, and a talk-back test tone (the **camera** beeps)
- system: decoded status/device info/date-time/Wi-Fi readouts, date/time
  sync, Wi-Fi scan, device users, reboot

HTTP endpoints, if you want them directly: `/{dev_id}/v` (MJPEG),
`/{dev_id}/snapshot`, `/{dev_id}/audio` (streaming WAV), `/{dev_id}/params`,
`/{dev_id}/info`, `/{dev_id}/wifi-scan`, `/{dev_id}/users`, and
`POST /{dev_id}/c/{command}`.

## Development tools

- **`binary_camera.py`** — a local protocol simulator: answers discovery,
  login, video (synthetic JPEG frames), snapshots, video params, date/time,
  Wi-Fi and user blocks on UDP 32108 without any hardware.
  `python binary_camera.py [port]`
- **`proxy_camera.py`** — a transparent DID-rewriting relay: advertise a fake
  DID to the vendor app and forward everything to a real camera, logging each
  decoded control packet in both directions (video/audio never logged).
  `python proxy_camera.py --did PROX-000001-CAMERA --target-ip 192.168.1.50`

## Troubleshooting

If you encounter issues:
1. Verify that your camera supports the PPPP protocol. The tested cameras had prefix DGOK, BATE, PTZA, FTYC, ...
    Little Stars app is not supported yet, as it uses a different protocol with ports 8070, 8080.
2. Check credential for the camera. Use -u and -p flags to specify username and password.
3. Check your camera in the same subnet as the machine with the script running.
4. Only one client at a time: if the vendor app is connected, the library cannot connect (and vice versa).
5. Run with `--log-level DEBUG` — every session line is tagged with the device
   ID, and the video path logs stream-header samples and reassembly state.

## Contributing

Contributions are welcome! Feel free to submit issues or pull requests on [GitHub](https://github.com/yourusername/aiopppp).

## License

This project is licensed under the Apache 2.0 License. See the [LICENSE](LICENSE) file for details.


## Thanks

This library is inspired and used protocol description from the following projects:

Protocol client implementations:

- https://github.com/DavidVentura/cam-reverse
- https://github.com/magicus/PPPP
- https://github.com/hyc/a9serv

- WireShark dissector for the PPPP protocol https://github.com/magicus/pppp-dissector
- Discussion at https://community.home-assistant.io/t/popular-a9-mini-wi-fi-camera-the-ha-challenge/230108
