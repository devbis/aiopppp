import asyncio
import functools
import logging
import math
import struct
import uuid

from aiohttp import web

from .const import (
    BinaryCommands,
    ChipType,
    DevType,
    LibError,
    VideoParamType,
    VideoResolution,
    VideoRotate,
    enum_name,
)
from .packets import parse_datetime_block, parse_user_block, parse_wifi_settings

logger = logging.getLogger(__name__)

SESSIONS = {}

# Parameters shown in the per-camera "current values" readout. Maps the
# symbolic name to the enum used to prettify the raw value (None = plain int).
READBACK_PARAMS = [
    ('resolution', VideoResolution, 'VIDEO_RESOLUTION_'),
    ('rotate', VideoRotate, 'VIDEO_ROTATE_'),
    ('bitrate', None, ''),
]

# 1 s of 440 Hz sine at 8 kHz signed 16-bit LE -- test tone for talk-back.
_TONE_PCM = b''.join(
    struct.pack('<h', int(8000 * math.sin(2 * math.pi * 440 * i / 8000)))
    for i in range(8000)
)


def _json_error(message, status):
    return web.json_response({'status': 'error', 'message': message}, status=status)


def _get_session(request):
    dev_id_str = request.match_info['dev_id']
    session = SESSIONS.get(dev_id_str)
    if session is None:
        return None, _json_error('unknown device', 404)
    return session, None


async def index(request):
    cameras = ''.join(
        f'<li><a href="/camera/{x}">{x}</a></li>' for x in SESSIONS.keys()
    ) or '<li><i>no cameras discovered yet</i></li>'
    return web.Response(
        text=(
            '<!doctype html><html><head><title>PPPP Cameras</title>'
            '<meta http-equiv="refresh" content="5"></head><body>'
            '<h1>PPPP Cameras</h1>'
            f'<ul>{cameras}</ul>'
            '<p><small>Page refreshes every 5 s as discovery finds cameras.</small></p>'
            '</body></html>'
        ),
        headers={'content-type': 'text/html'},
    )


def _camera_page_html(dev_id):
    js = '''
    <script>
    const DEV = document.location.pathname.split('/').pop();

    function setStatus(text, isError) {
        const el = document.getElementById('status');
        el.textContent = text;
        el.style.color = isError ? '#b00' : '#080';
    }

    // Params successfully SET this session -- some cameras (FTYC) apply a
    // param but never report it back, so the readout shows both.
    const lastSet = {};

    async function sendCommand(cmd, params) {
        setStatus(`${cmd} ...`, false);
        try {
            const resp = await fetch(`/${DEV}/c/${cmd}`, {
                method: 'POST',
                body: JSON.stringify(params || {}),
            });
            const data = await resp.json();
            if (data.status === 'ok') {
                setStatus(`${cmd}: ok`, false);
                if (cmd === 'set-video-param') lastSet[params.name] = params.value;
            } else {
                setStatus(`${cmd}: ${data.message}`, true);
            }
        } catch (e) {
            setStatus(`${cmd}: ${e}`, true);
        }
        return false;
    }

    async function loadParams() {
        const el = document.getElementById('params-current');
        el.textContent = 'reading...';
        try {
            const resp = await fetch(`/${DEV}/params`);
            const data = await resp.json();
            el.textContent = '';
            let first = true;
            for (const [name, p] of Object.entries(data.params || {})) {
                if (!first) el.append(' | ');
                first = false;
                const span = document.createElement('span');
                if (p.error) {
                    span.textContent = `${name}: <${p.error}>`;
                } else {
                    const reported = p.symbol !== null ? p.symbol : p.value;
                    let text = `${name}: ${reported}`;
                    if (name in lastSet && String(lastSet[name]) !== String(reported)) {
                        text += ` (set to ${lastSet[name]}, not reported back)`;
                    }
                    span.textContent = text;
                    span.title = `raw: ${p.raw}`;   // hex payload on hover
                    const sel = document.getElementById(`param-${name}`);
                    if (sel && p.symbol !== null && !(name in lastSet)) sel.value = p.symbol;
                }
                el.append(span);
            }
            if (first) el.textContent = 'no data';
        } catch (e) {
            el.textContent = `failed: ${e}`;
        }
    }

    async function loadInto(path, label) {
        const el = document.getElementById('info-dump');
        el.textContent = `${label}: reading...`;
        try {
            const resp = await fetch(`/${DEV}/${path}`);
            el.textContent = `${label}:\n` + JSON.stringify(await resp.json(), null, 2);
        } catch (e) {
            el.textContent = `${label} failed: ${e}`;
        }
    }

    function loadInfo() { return loadInto('info', 'device info'); }

    function setAlias() {
        const name = document.getElementById('alias-input').value;
        if (name) sendCommand('set-alias', {name: name});
    }

    function ptzPreset(cmd) {
        const index = +document.getElementById('preset-index').value;
        sendCommand(cmd, {index: index});
    }

    // --- low-latency audio: fetch the WAV stream and schedule raw PCM via
    // Web Audio. The <audio> element buffers many seconds of an endless WAV
    // before it starts; this path plays with ~100 ms latency instead.
    let audioCtx = null, audioAbort = null;

    function stopLowLatencyAudio() {
        if (audioAbort) { audioAbort.abort(); audioAbort = null; }
        if (audioCtx) { audioCtx.close(); audioCtx = null; }
    }

    async function startLowLatencyAudio() {
        stopLowLatencyAudio();
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        audioAbort = new AbortController();
        let playhead = 0;
        let pending = new Uint8Array(0);
        let skip = 44;  // WAV header
        try {
            const resp = await fetch(`/${DEV}/audio`, {signal: audioAbort.signal});
            const reader = resp.body.getReader();
            setStatus('audio: playing (low-latency)', false);
            while (audioCtx) {
                const {done, value} = await reader.read();
                if (done) break;
                let buf = value;
                if (skip > 0) {
                    const n = Math.min(skip, buf.length);
                    buf = buf.subarray(n);
                    skip -= n;
                    if (!buf.length) continue;
                }
                const merged = new Uint8Array(pending.length + buf.length);
                merged.set(pending);
                merged.set(buf, pending.length);
                const even = merged.length & ~1;
                pending = merged.slice(even);
                if (!even) continue;
                const samples = new Int16Array(merged.buffer, 0, even / 2);
                const ab = audioCtx.createBuffer(1, samples.length, 8000);
                const ch = ab.getChannelData(0);
                for (let i = 0; i < samples.length; i++) ch[i] = samples[i] / 32768;
                const src = audioCtx.createBufferSource();
                src.buffer = ab;
                src.connect(audioCtx.destination);
                const now = audioCtx.currentTime;
                if (playhead < now + 0.05) playhead = now + 0.05;  // fell behind: jump
                src.start(playhead);
                playhead += ab.duration;
            }
        } catch (e) {
            if (e.name !== 'AbortError') setStatus(`audio: ${e}`, true);
        }
    }

    window.addEventListener('load', loadParams);
    </script>
    '''
    x = dev_id
    body = (
        f'<p><a href="/">&larr; all cameras</a></p>'
        f'<h1>{x}</h1>'
        '<div id="status" style="min-height:1.2em;font-family:monospace"></div>'

        f'<img src="/{x}/v"/><br/>'
        f'<button onClick="sendCommand(\'start-video\')">Start Video</button>'
        f'<button onClick="sendCommand(\'stop-video\')">Stop Video</button>'
        f'<a href="/{x}/snapshot" target="_blank"><button>Snapshot</button></a>'

        '<h3>PTZ</h3>'
        f'<button onClick="sendCommand(\'rotate\', {{value: \'LEFT\'}})">LEFT</button>'
        f'<button onClick="sendCommand(\'rotate\', {{value: \'RIGHT\'}})">RIGHT</button>'
        f'<button onClick="sendCommand(\'rotate\', {{value: \'UP\'}})">UP</button>'
        f'<button onClick="sendCommand(\'rotate\', {{value: \'DOWN\'}})">DOWN</button>'
        f'<button onClick="sendCommand(\'rotate-stop\')">Rotate STOP</button>'
        ' &nbsp; Preset: <input id="preset-index" type="number" value="1" min="0" max="255" style="width:4em">'
        '<button onClick="ptzPreset(\'ptz-preset-goto\')">Goto</button>'
        '<button onClick="ptzPreset(\'ptz-preset-set\')">Save</button>'
        '<br/><small>Presets are unimplemented on PTZA and FTYC &mdash; every prefab '
        'op was tried by hand on 2026-08-25 and none of them stores, recalls or '
        'moves anything. The buttons stay for other firmwares.</small>'

        '<h3>Lights</h3>'
        f'<button onClick="sendCommand(\'toggle-lamp\', {{value: 1}})">Light ON</button>'
        f'<button onClick="sendCommand(\'toggle-lamp\', {{value: 0}})">Light OFF</button>'
        f'<button onClick="sendCommand(\'toggle-ir\', {{value: 1}})">IR ON</button>'
        f'<button onClick="sendCommand(\'toggle-ir\', {{value: 0}})">IR OFF</button>'

        '<h3>Video parameters</h3>'
        '<div id="params-current" style="font-family:monospace">not read yet</div>'
        '<button onClick="loadParams()">Re-read params</button><br/>'
        ' Resolution: '
        '<select id="param-resolution" onChange="sendCommand(\'set-video-param\', {name: \'resolution\', value: this.value})">'
        '<option>QVGA</option><option>VGA</option><option>HD</option><option>FD</option><option>UD</option>'
        '</select>'
        ' Rotate: '
        '<select id="param-rotate" onChange="sendCommand(\'set-video-param\', {name: \'rotate\', value: this.value})">'
        '<option>NORMAL</option><option>H</option><option>V</option><option>HV</option>'
        '</select>'
        ' Bitrate: '
        '<input type="range" min="0" max="100" '
        'onChange="sendCommand(\'set-video-param\', {name: \'bitrate\', value: +this.value})">'

        '<h3>Audio</h3>'
        '<button onClick="startLowLatencyAudio()">Play (low-latency)</button>'
        '<button onClick="stopLowLatencyAudio()">Stop</button> '
        f'<button onClick="sendCommand(\'talk-test\')">Talk test tone (1s)</button>'
        f'<br/><small>Buffered fallback (several seconds behind): '
        f'<audio controls preload="none" src="/{x}/audio"></audio> '
        f'<button onClick="sendCommand(\'stop-audio\')">Stop Audio</button><br/>'
        'FTYC-style cameras deliver audio muxed with video &mdash; audio only '
        'flows while the video stream is running.</small>'

        '<h3>System</h3>'
        '<button onClick="loadInfo()">Load device info</button> '
        '<button onClick="loadInto(\'wifi-scan\', \'wifi scan\')">Scan Wi-Fi (~10s)</button> '
        '<button onClick="loadInto(\'users\', \'device users\')">Show users</button> '
        'Alias: <input id="alias-input" type="text" style="width:10em">'
        '<button onClick="setAlias()">Set</button> '
        f'<button onClick="sendCommand(\'sync-datetime\')">Sync date/time</button> '
        f'<button onClick="if (confirm(\'Reboot camera?\')) sendCommand(\'reboot\')">Reboot</button>'
        '<pre id="info-dump" style="background:#f4f4f4;padding:0.5em"></pre>'
    )
    return (
        '<!doctype html><html><head><title>{}</title></head><body>{}{}</body></html>'.format(dev_id, js, body)
    )


async def camera_page(request):
    session, err = _get_session(request)
    if err:
        return err
    return web.Response(
        text=_camera_page_html(request.match_info['dev_id']),
        headers={'content-type': 'text/html'},
    )


async def handle_commands(request):
    session, err = _get_session(request)
    if err:
        return err
    cmd = request.match_info['cmd']
    params = await request.json()

    async def talk_test(**kwargs):
        # 1 s test tone in 120 ms chunks (960 samples = 1920 PCM bytes) --
        # the same chunking the camera uses for its own audio -- paced in
        # real time so the camera's jitter buffer isn't flooded.
        await session.start_talk()
        try:
            for i in range(0, len(_TONE_PCM), 1920):
                await session.send_audio(_TONE_PCM[i:i + 1920])
                await asyncio.sleep(0.12)
        finally:
            await session.stop_talk()

    async def sync_datetime(**kwargs):
        await session.set_datetime()

    web2cmd = {
        'toggle-lamp': getattr(session, 'toggle_whitelight', None),
        'toggle-ir': getattr(session, 'toggle_ir', None),
        'rotate': getattr(session, 'step_rotate', None),
        'rotate-stop': getattr(session, 'rotate_stop', None),
        'reboot': getattr(session, 'reboot', None),
        'start-video': getattr(session, 'start_video', None),
        'stop-video': getattr(session, 'stop_video', None),
        'set-video-param': getattr(session, 'set_video_param', None),
        'ptz-preset-goto': getattr(session, 'ptz_goto_preset', None),
        'ptz-preset-set': getattr(session, 'ptz_set_preset', None),
        'set-alias': getattr(session, 'set_alias', None),
        'sync-datetime': sync_datetime if hasattr(session, 'set_datetime') else None,
        'start-audio': getattr(session, 'start_audio', None),
        'stop-audio': getattr(session, 'stop_audio', None),
        'talk-test': talk_test if hasattr(session, 'start_talk') else None,
    }

    if cmd not in web2cmd:
        return _json_error('unknown command', 404)
    handler = web2cmd[cmd]
    if handler is None:
        return _json_error('command not supported by this device', 501)

    try:
        await handler(**params)
    except Exception as e:
        # Surface the failure to the browser -- a silent 500 here makes a
        # server-side error indistinguishable from "camera ignored it".
        logger.exception('Command %s failed for %s', cmd, request.match_info['dev_id'])
        return _json_error(f'{type(e).__name__}: {e}', 500)
    return web.json_response({'status': 'ok'})


async def get_params(request):
    """Read back current video parameters (ENH-003). Values are best-effort
    decoded; the raw ACK payload is always included."""
    session, err = _get_session(request)
    if err:
        return err
    if not hasattr(session, 'get_video_param'):
        return _json_error('not supported by this device', 501)

    result = {}
    # Sequential on purpose: wait_cmd_result is keyed by command, concurrent
    # VIDEOPARAM_GETs would race each other.
    for name, enum_cls, prefix in READBACK_PARAMS:
        try:
            payload = await session.get_video_param(name, timeout=3)
        except Exception as e:
            result[name] = {'error': f'{type(e).__name__}: {e}'}
            continue
        value = session.decode_video_param(payload, name)
        symbol = None
        if value is not None and enum_cls is not None:
            try:
                symbol = enum_cls(value).name.replace(prefix, '')
            except ValueError:
                pass
        result[name] = {'value': value, 'symbol': symbol, 'raw': payload.hex(' ')}
    return web.json_response({'status': 'ok', 'params': result})


async def get_info(request):
    """System/network readout (ENH-004). Parsed status plus raw hex blocks for
    the calls whose struct layout is firmware-specific."""
    session, err = _get_session(request)
    if err:
        return err
    if not hasattr(session, 'get_status'):
        return _json_error('not supported by this device', 501)

    def decode_device_info(data):
        # The 528-byte INF block is mostly zeros on the tested hardware; only
        # the leading version field is understood so far.
        out = {}
        if len(data) >= 4:
            out['swVersion'] = '.'.join(str(b) for b in reversed(data[:4]))
            # Same packing as the status block's swVer: byte 1 device type,
            # byte 3 chip type.
            sw = struct.unpack('<I', data[:4])[0]
            dev_type = (sw >> 8) & 0xFF
            chip_type = (sw >> 24) & 0xFF
            out['devType'] = dev_type
            out['devTypeName'] = enum_name(DevType, dev_type, 'DEV_')
            out['chipType'] = chip_type
            out['chipTypeName'] = enum_name(ChipType, chip_type, 'CHP_')
        out['raw'] = data.hex(' ')
        return out

    def decode_datetime(data):
        return parse_datetime_block(data) or {'raw': data.hex(' ')}

    def decode_wifi(data):
        return parse_wifi_settings(data) or {'raw': data.hex(' ')}

    info = {}
    for key, call, decode in [
        ('status', session.get_status, None),
        # Short timeouts: cameras that don't implement a block shouldn't stall
        # the whole endpoint for the default 5 s each.
        ('device_info', functools.partial(session.get_device_info, timeout=3)
         if hasattr(session, 'get_device_info') else None, decode_device_info),
        ('datetime', functools.partial(session.get_datetime, timeout=3)
         if hasattr(session, 'get_datetime') else None, decode_datetime),
        ('wifi', functools.partial(session.get_wifi_settings, timeout=3)
         if hasattr(session, 'get_wifi_settings') else None, decode_wifi),
    ]:
        if call is None:
            continue
        try:
            value = await call()
        except Exception as e:
            info[key] = f'error: {type(e).__name__}: {e}'
            continue
        if isinstance(value, bytes):
            value = decode(value) if decode else value.hex(' ')
        info[key] = value

    # Login state, plus the reason for anything the camera turned down -- a
    # refusal answers with an empty payload, so otherwise there is nothing
    # above to say why a block came back blank.
    info['auth'] = getattr(session, 'dev_properties', {}).get('auth')
    refused = {
        BinaryCommands(cmd).name: f'{code.name} ({code.value})'
        for cmd, code in getattr(session, 'cmd_results', {}).items()
        if code is not None and code < LibError.OK
    }
    if refused:
        info['refused'] = refused
    return web.json_response({'status': 'ok', 'info': info})


async def wifi_scan(request):
    """Trigger a Wi-Fi scan and return the raw result plus a best-effort list
    of SSID-looking printable strings (the scan-list struct is unverified)."""
    session, err = _get_session(request)
    if err:
        return err
    if not hasattr(session, 'scan_wifi'):
        return _json_error('not supported by this device', 501)
    try:
        data = await session.scan_wifi(timeout=12)
    except Exception as e:
        return _json_error(f'{type(e).__name__}: {e}', 500)
    # Pull out printable ASCII runs as candidate SSIDs to make the raw
    # dump readable; the exact record layout is still unknown.
    candidates, run = [], bytearray()
    for b in data:
        if 32 <= b < 127:
            run.append(b)
        else:
            if len(run) >= 3:
                candidates.append(run.decode('ascii'))
            run = bytearray()
    if len(run) >= 3:
        candidates.append(run.decode('ascii'))
    return web.json_response({
        'status': 'ok',
        'length': len(data),
        'strings': candidates[:50],
        'raw': data.hex(' '),
    })


async def get_users(request):
    """Configured device users (CMD_SYSTEM_USER_GET), decoded per the vendor
    app's layout."""
    session, err = _get_session(request)
    if err:
        return err
    if not hasattr(session, 'get_users'):
        return _json_error('not supported by this device', 501)
    try:
        data = await session.get_users(timeout=5)
    except Exception as e:
        return _json_error(f'{type(e).__name__}: {e}', 500)
    return web.json_response({
        'status': 'ok',
        'user': parse_user_block(data) or None,
        'length': len(data),
        'raw': data.hex(' '),
    })


async def get_snapshot(request):
    """Still image (ENH-002). CMD_SNAPSHOT_GET goes unanswered on all tested
    hardware (FTYC + PTZA), so fall back to the latest reassembled video
    frame; the x-snapshot-source header says which path served the image."""
    session, err = _get_session(request)
    if err:
        return err

    data, source = b'', 'camera'
    if hasattr(session, 'get_snapshot'):
        try:
            data = await session.get_snapshot(timeout=3)
        except Exception:
            data = b''
    if not data:
        frame = getattr(session.frame_buffer, 'latest_frame', None)
        if frame is not None:
            data, source = frame.data, 'video-frame'
    if not data:
        return _json_error(
            'camera did not answer SNAPSHOT_GET and no video frame is buffered'
            ' -- start the video stream once and retry', 504)
    return web.Response(body=data, headers={
        'content-type': 'image/jpeg',
        'cache-control': 'no-store',
        'x-snapshot-source': source,
    })


def _wav_header(sample_rate=8000):
    # Unknown-length stream: RIFF/data sizes are set to 0xFFFFFFFF, which
    # browsers accept for live playback.
    byte_rate = sample_rate * 2
    return (
        b'RIFF' + struct.pack('<I', 0xFFFFFFFF) + b'WAVE'
        b'fmt ' + struct.pack('<IHHIIHH', 16, 1, 1, sample_rate, byte_rate, 2, 16) +
        b'data' + struct.pack('<I', 0xFFFFFFFF)
    )


# Live /audio listeners per device. A plain "did I start it" boolean raced:
# when a new listener connected while the previous one (an aborted fetch, the
# fallback <audio> element) was still tearing down, the old teardown stopped
# the camera stream AFTER the new listener attached -- audio played for a
# second and then starved. Only the last listener out stops the camera.
_AUDIO_LISTENERS = {}


async def stream_audio(request):
    """Live audio as a streaming WAV (ENH-005). Starts the camera audio stream
    for the first listener; the camera is stopped only when the last listener
    disconnects."""
    session, err = _get_session(request)
    if err:
        return err
    if not hasattr(session, 'start_audio'):
        return _json_error('not supported by this device', 501)
    dev_id = request.match_info['dev_id']

    response = web.StreamResponse()
    response.content_type = 'audio/wav'
    await response.prepare(request)

    _AUDIO_LISTENERS[dev_id] = _AUDIO_LISTENERS.get(dev_id, 0) + 1
    # Always (re)request: if a concurrent teardown just stopped the camera,
    # is_audio_requested is False again and this re-arms it; otherwise
    # start_audio is a no-op.
    await session.start_audio()
    try:
        await response.write(_wav_header())
        while True:
            frame = await session.get_audio_frame()
            await response.write(frame.data)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        _AUDIO_LISTENERS[dev_id] = _AUDIO_LISTENERS.get(dev_id, 1) - 1
        if _AUDIO_LISTENERS[dev_id] <= 0:
            _AUDIO_LISTENERS.pop(dev_id, None)
            try:
                await session.stop_audio()
            except Exception:
                logger.debug('stop_audio on disconnect failed', exc_info=True)
    return response


async def stream_video(request):
    session, err = _get_session(request)
    if err:
        return err

    response = web.StreamResponse()
    boundary = '--frame' + uuid.uuid4().hex
    response.content_type = f'multipart/x-mixed-replace; boundary={boundary}'
    response.content_length = 1000000000000

    await response.prepare(request)
    if not session.is_video_requested:
        await session.start_video()

    frame_buffer = session.frame_buffer

    while True:
        frame = await frame_buffer.get()
        if not frame.data:
            continue
        header = f'--{boundary}\r\n'.encode()
        header += b'Content-Length: %d\r\n' % len(frame.data)
        header += b'Content-Type: image/jpeg\r\n\r\n'
        try:
            await response.write(header)
            await response.write(frame.data)
        except ConnectionResetError:
            logger.warning('Connection reset')
            break
    return response


async def start_web_server(port=4000):
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/camera/{dev_id}', camera_page)
    app.router.add_get('/{dev_id}/v', stream_video)
    app.router.add_get('/{dev_id}/snapshot', get_snapshot)
    app.router.add_get('/{dev_id}/params', get_params)
    app.router.add_get('/{dev_id}/info', get_info)
    app.router.add_get('/{dev_id}/wifi-scan', wifi_scan)
    app.router.add_get('/{dev_id}/users', get_users)
    app.router.add_get('/{dev_id}/audio', stream_audio)
    app.router.add_post('/{dev_id}/c/{cmd}', handle_commands)

    runner = web.AppRunner(app, handle_signals=True)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    try:
        logger.info(f'Starting web server on port {port}')
        await site.start()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass
    finally:
        logger.info('Shutting down web server')
        await runner.cleanup()
