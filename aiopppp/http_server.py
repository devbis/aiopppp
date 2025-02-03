import asyncio
import logging

from aiohttp import web

logger = logging.getLogger(__name__)

SESSIONS = {}


async def index(request):
    js = '''
    <script>
    function sendCommand(dev_id, cmd, params) {
        var par = new URLSearchParams(params).toString()
        fetch(`/${dev_id}/c/${cmd}`, {
            method: 'POST',
            body: JSON.stringify(params),
        });
        return false;
    }
    </script>
    '''
    videos = '<hr/>'.join(
        f'<h2>{x}</h2><img src=\"/{x}/v\"/><br/>'
        f'<button onClick="sendCommand(\'{x}\', \'toggle-lamp\', {{value: 1}})">ON</button>'
        f'<button onClick="sendCommand(\'{x}\', \'toggle-lamp\', {{value: 0}})">OFF</button>'
        f'<button onClick="sendCommand(\'{x}\', \'toggle-ir\', {{value: 1}})">IR ON</button>'
        f'<button onClick="sendCommand(\'{x}\', \'toggle-ir\', {{value: 0}})">IR OFF</button>'
        '<br>'
        f'<button onClick="sendCommand(\'{x}\', \'rotate\', {{value: \'LEFT\'}})">LEFT</button>'
        f'<button onClick="sendCommand(\'{x}\', \'rotate\', {{value: \'RIGHT\'}})">RIGHT</button>'
        f'<button onClick="sendCommand(\'{x}\', \'rotate\', {{value: \'UP\'}})">UP</button>'
        f'<button onClick="sendCommand(\'{x}\', \'rotate\', {{value: \'DOWN\'}})">DOWN</button>'
        f'<button onClick="sendCommand(\'{x}\', \'rotate-stop\', {{}})">Rotate STOP</button>'
        '<br>'
        f'<button onClick="sendCommand(\'{x}\', \'reboot\', {{}})">Reboot</button>'
        for x in SESSIONS.keys())
    return web.Response(
        text="<!doctype html><html><head><title>PPPP Cameras</title></head><body>{}<h1>PPPP Cameras</h1>{}</body></html>".format(
            js,
            videos,
        ),
        headers={'content-type': 'text/html'},
    )

async def handle_commands(request):
    dev_id_str = request.match_info['dev_id']
    cmd = request.match_info['cmd']
    params = await request.json()
    session = SESSIONS[dev_id_str]
    web2cmd = {
        'toggle-lamp': session.toggle_whitelight,
        'toggle-ir': session.toggle_ir,
        'rotate': session.step_rotate,
        'rotate-stop': session.rotate_stop,
        'reboot': session.reboot,
        'reset': session.reset,
    }.get(cmd)

    if web2cmd is None:
        return web.Response(text='{"status": "error", "message": "unknown command"}', headers={'content-type': 'application/json'}, status=404)

    await web2cmd(**params)
    return web.Response(text='{"status": "ok"}', headers={'content-type': 'application/json'})


async def stream_video(request):
    response = web.StreamResponse()
    response.content_type = 'multipart/x-mixed-replace; boundary=frame'

    await response.prepare(request)

    dev_id_str = request.match_info['dev_id']
    frame_buffer = SESSIONS[dev_id_str].frame_buffer

    while True:
        frame = await frame_buffer.get()
        header = b'--frame\r\n'
        header += b'Content-Type: image/jpeg\r\n\r\n'
        await response.write(header)
        await response.write(frame.data)


async def start_web_server(port=4000):
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/{dev_id}/v', stream_video)
    app.router.add_post('/{dev_id}/c/{cmd}', handle_commands)

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
