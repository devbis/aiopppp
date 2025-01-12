# aiopppp

**aiopppp** is an asynchronous Python library designed to simplify connecting to and interacting with cameras that utilize the Peer-to-Peer Protocol (PPPP). 
This library enables seamless communication with compatible cameras for live video streaming,
capturing snapshots, or configuring camera settings, all using asyncio for efficient performance.

## Features

- Initial camera discovery (plain, and encoded)
- Asynchronous peer-to-peer connections with PPPP-enabled cameras using JSON control protocol
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

Here’s an example of how to use the library:

```python
import asyncio
from aiopppp import Discovery, JsonSession

async def on_device_found(device):
    print(f"Found device: {device}")
    session = JsonSession(device)
    session.start()

    while True:
        frame = await session.frame_queue.get()
        
        # Do something with the frame
        # await response.write(b'--frame\r\nContent-Type: image/jpeg\r\n\r\n')
        # await response.write(frame.data)
        print(f"Frame received: {frame}")
        
async def main():
    discovery = Discovery()
    await discovery.discover(on_device_found)

    
# Run the async main function
asyncio.run(main())
```

## Running test web server

To test the library, you can run a simple web server that streams the camera feed.
The server will automatically discover the camera and start streaming the video feed.

```bash
python -m aiopppp
```

Then, visit `http://localhost:4000` in your browser to view the camera feed.

## Troubleshooting

If you encounter issues:
1. Verify that your camera supports the PPPP protocol with JSON commands. The tested camera had prefix DGOK.
2. Check your camera in the same subnet as the machine with the script running.

## Contributing

Contributions are welcome! Feel free to submit issues or pull requests on [GitHub](https://github.com/yourusername/aiopppp).

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
