"""G.711 (A-law / u-law) codecs.

The cheap PPPP cameras carry audio as 8 kHz 8-bit G.711 (A-law on the iLnk
firmware, u-law on some others). We decode to signed 16-bit little-endian PCM
for playback and encode PCM back for talk-back.

Pure Python and dependency-free on purpose: the stdlib ``audioop`` module that
would normally do this was removed in Python 3.13, and aiopppp targets 3.7+.
Ported from the reference Sun ``g711.c``; decode tables are precomputed at
import, encode uses the standard segment search.
"""

_SIGN_BIT = 0x80
_QUANT_MASK = 0x0F
_SEG_SHIFT = 4
_SEG_MASK = 0x70

_SEG_AEND = (0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF)
_SEG_UEND = (0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF)
_BIAS = 0x84
_CLIP = 8159


def _search(val, table):
    for i, end in enumerate(table):
        if val <= end:
            return i
    return len(table)


def _alaw2linear(a_val):
    a_val ^= 0x55
    t = (a_val & _QUANT_MASK) << 4
    seg = (a_val & _SEG_MASK) >> _SEG_SHIFT
    if seg == 0:
        t += 8
    elif seg == 1:
        t += 0x108
    else:
        t += 0x108
        t <<= seg - 1
    return t if (a_val & _SIGN_BIT) else -t


def _linear2alaw(pcm_val):
    pcm_val >>= 3
    if pcm_val >= 0:
        mask = 0xD5
    else:
        mask = 0x55
        pcm_val = -pcm_val - 1
    seg = _search(pcm_val, _SEG_AEND)
    if seg >= 8:
        return 0x7F ^ mask
    aval = seg << _SEG_SHIFT
    if seg < 2:
        aval |= (pcm_val >> 1) & _QUANT_MASK
    else:
        aval |= (pcm_val >> seg) & _QUANT_MASK
    return aval ^ mask


def _ulaw2linear(u_val):
    u_val = ~u_val & 0xFF
    t = ((u_val & _QUANT_MASK) << 3) + _BIAS
    t <<= (u_val & _SEG_MASK) >> _SEG_SHIFT
    return (_BIAS - t) if (u_val & _SIGN_BIT) else (t - _BIAS)


def _linear2ulaw(pcm_val):
    pcm_val >>= 2
    if pcm_val < 0:
        pcm_val = -pcm_val
        mask = 0x7F
    else:
        mask = 0xFF
    if pcm_val > _CLIP:
        pcm_val = _CLIP
    pcm_val += _BIAS >> 2
    seg = _search(pcm_val, _SEG_UEND)
    if seg >= 8:
        return 0x7F ^ mask
    uval = (seg << 4) | ((pcm_val >> (seg + 1)) & 0xF)
    return (uval ^ mask) & 0xFF


# Precomputed decode tables: G.711 code -> signed 16-bit sample.
_ALAW_DECODE = [_alaw2linear(a) for a in range(256)]
_ULAW_DECODE = [_ulaw2linear(u) for u in range(256)]


def _clamp16(v):
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


def alaw_decode(data: bytes) -> bytes:
    """A-law bytes -> signed 16-bit little-endian PCM."""
    out = bytearray(len(data) * 2)
    for i, b in enumerate(data):
        s = _clamp16(_ALAW_DECODE[b]) & 0xFFFF
        out[2 * i] = s & 0xFF
        out[2 * i + 1] = (s >> 8) & 0xFF
    return bytes(out)


def ulaw_decode(data: bytes) -> bytes:
    """u-law bytes -> signed 16-bit little-endian PCM."""
    out = bytearray(len(data) * 2)
    for i, b in enumerate(data):
        s = _clamp16(_ULAW_DECODE[b]) & 0xFFFF
        out[2 * i] = s & 0xFF
        out[2 * i + 1] = (s >> 8) & 0xFF
    return bytes(out)


def alaw_encode(pcm: bytes) -> bytes:
    """Signed 16-bit little-endian PCM -> A-law bytes."""
    out = bytearray(len(pcm) // 2)
    for i in range(len(out)):
        sample = int.from_bytes(pcm[2 * i:2 * i + 2], 'little', signed=True)
        out[i] = _linear2alaw(sample)
    return bytes(out)


def ulaw_encode(pcm: bytes) -> bytes:
    """Signed 16-bit little-endian PCM -> u-law bytes."""
    out = bytearray(len(pcm) // 2)
    for i in range(len(out)):
        sample = int.from_bytes(pcm[2 * i:2 * i + 2], 'little', signed=True)
        out[i] = _linear2ulaw(sample)
    return bytes(out)


CODECS = {
    'alaw': (alaw_decode, alaw_encode),
    'ulaw': (ulaw_decode, ulaw_encode),
}
