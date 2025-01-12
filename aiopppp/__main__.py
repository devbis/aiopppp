import asyncio
import logging

from aiohttp import web

from .discover import Discovery
from .session import JsonSession

frame_queues = {}
sessions = {}
logger = logging.getLogger(__name__)


async def index(request):
    videos = '<hr/>'.join(f'<h2>{x}</h2><img src=\"/{x}/v\"/>' for x in sessions.keys())
    return web.Response(
        text="<!doctype html><html><head><title>PPPP Cameras</title></head><body><h1>PPPP Cameras</h1>{}</body></html>".format(
            videos,
        ),
        headers={'content-type': 'text/html'},
    )


async def stream_video(request):
    response = web.StreamResponse()
    response.content_type = 'multipart/x-mixed-replace; boundary=frame'

    await response.prepare(request)

    dev_id_str = request.match_info['dev_id']
    frame_queue = frame_queues[dev_id_str]

    while True:
        frame = await frame_queue.get()
        header = b'--frame\r\n'
        header += b'Content-Type: image/jpeg\r\n\r\n'
        await response.write(header)
        await response.write(frame.data)


async def start_web_server(port=4000):
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/{dev_id}/v', stream_video)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port, reuse_port=True)
    try:
        logger.info(f'Starting web server on port {port}')
        await site.start()
        while True:
            await asyncio.sleep(1)
    finally:
        logger.info('Shutting down web server')
        await runner.cleanup()


def on_device_found(device):
    session = JsonSession(device)
    sessions[device.dev_id.dev_id] = session
    frame_queues[device.dev_id.dev_id] = session.frame_queue
    session.start()


async def amain():
    # await start_web_server()
    discovery = Discovery()
    # web_task = asyncio.create_task(start_web_server())
    await asyncio.gather(discovery.discover(on_device_found), start_web_server())


def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(amain())


if __name__ == '__main__':
    main()
