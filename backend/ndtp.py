"""NDTP 6.2 NPL/NPH framing and Nav00, with an explicit private replay channel.

Service 65500 carries JSON simulator metadata (not physical sensor cells). The
packet metadata precedes its standard Nav00 frame and preserves CSV precision,
missing fields, packet ID and receive/event clocks, which Nav00 alone cannot.
"""
import asyncio
import json
import struct

NPL = struct.Struct('<HHHHBIH')
NPH = struct.Struct('<HHHI')
NAV = struct.Struct('<IIIBBHHHHHBB')
REPLAY_SERVICE = 65500
MAX_BODY = 65525


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return ((crc & 255) << 8) | (crc >> 8)


def frame(service, kind, body, peer=0, request=0):
    data = NPH.pack(service, kind, 1, request & 0xFFFFFFFF) + body
    if len(data) > 65535:
        raise ValueError('Oversized NDTP frame')
    return NPL.pack(0x7E7E, len(data), 0, crc16(data), 2, peer, 0) + data


def control(value, request=0):
    return frame(REPLAY_SERVICE, 1, json.dumps(value, allow_nan=False, separators=(',', ':')).encode(), request=request)


def handshake(peer):
    return frame(0, 100, struct.pack('<HHHIII', 6, 2, 0, peer, 65535, 0), peer)


def navigation(packet):
    def integer(v, maximum):
        return max(0, min(maximum, round(v or 0)))
    flags = (32 if (packet.lat or 0) >= 0 else 0) | (64 if (packet.lon or 0) >= 0 else 0) | (128 if packet.valid else 0)
    body = bytes([0, 0]) + NAV.pack(integer(packet.event, 2**32-1),
        integer(abs(packet.lon or 0)*1e7, 2**32-1), integer(abs(packet.lat or 0)*1e7, 2**32-1),
        flags, 0, integer(packet.speed, 65535), integer(packet.speed, 65535),
        integer(packet.heading, 65535), 0, integer(packet.alt, 65535), 0, 0)
    return frame(1, 101, body, int(packet.unit_id), packet.seq)


async def read_frame(reader):
    header = await reader.readexactly(NPL.size)
    sig, length, flags, crc, kind, peer, _ = NPL.unpack(header)
    if sig != 0x7E7E or kind != 2 or length < NPH.size or flags != 0:
        raise ValueError('Unsupported or invalid NPL header')
    data = await reader.readexactly(length)
    if crc16(data) != crc:
        raise ValueError('NDTP CRC mismatch')
    service, typ, _, request = NPH.unpack(data[:NPH.size])
    return service, typ, peer, request, data[NPH.size:]


def decode_navigation(body, peer, request, receive):
    if len(body) < 28 or body[:2] != b'\x00\x00':
        raise ValueError('Nav00 must be the first complete cell')
    stamp, lon, lat, flags, _, speed, _, course, _, alt, _, _ = NAV.unpack(body[2:28])
    return {'unit_id': str(peer), 'packet_id': str(request), 'event_time': stamp,
            'receive_time': receive, 'lon': lon/1e7*(1 if flags & 64 else -1),
            'lat': lat/1e7*(1 if flags & 32 else -1), 'speed': speed,
            'heading': course, 'alt': alt, 'location_valid': bool(flags & 128)}
