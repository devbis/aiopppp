import asyncio
import logging

from aiohttp import web


logger = logging.getLogger(__name__)

SESSIONS = {}


async def index(request):
    videos = '<hr/>'.join(f'<h2>{x}</h2><img src=\"/{x}/v\"/>' for x in SESSIONS.keys())
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
    frame_queue = SESSIONS[dev_id_str].frame_queue

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
