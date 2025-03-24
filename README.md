# aiopppp

**aiopppp** is an asynchronous Python library designed to simplify connecting to and interacting with cameras that 
utilize the Peer-to-Peer Protocol (PPPP) which is implemented in some cheap cameras (A9, X5, etc.) 
This library enables seamless communication with compatible cameras for live video streaming,
capturing snapshots, or configuring camera settings, all using asyncio for efficient performance.

## Features

- Initial camera discovery (plain and encoded (not all keys))
- Asynchronous peer-to-peer connections with PPPP-enabled cameras using both JSON and binary control protocols
- Stream live video feeds directly from the camera.
- Remote camera rotation
- (TBD) Capture snapshots and save them locally.
- (TBD) Configure and manage camera settings.
- Lightweight and easy to integrate into Python applications.

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

## Running test web server

To test the library, you can run a simple web server that streams the camera feed.
The server will automatically discover the camera and start streaming the video feed.

```bash
python -m aiopppp -u admin -p 6666
```

Then, visit `http://localhost:4000` in your browser to view the camera feed.

## Troubleshooting

If you encounter issues:
1. Verify that your camera supports the PPPP protocol. The tested cameras had prefix DGOK, BATE. 
    Little Stars app is not supported yet, as it uses a different protocol with ports 8070, 8080.
2. Check credential for the camera. Use -u and -p flags to specify username and password.
3. Check your camera in the same subnet as the machine with the script running.

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
