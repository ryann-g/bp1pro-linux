"""Minimal Bluetooth SDP client — asks the earbuds for the real SPP channel.

The Baseus buds register their RFCOMM service with a channel number that is
not guessable; Android apps get it from SDP, so we do the same: connect a raw
L2CAP socket to PSM 1 (the SDP server), send one
SDP_ServiceSearchAttributeRequest for UUID16 0x1101 (Serial Port) with
attribute id 0x0004 (ProtocolDescriptorList), and read the RFCOMM channel.

No external dependencies (raw AF_BLUETOOTH only).
"""

from __future__ import annotations

import logging
import socket
import struct

log = logging.getLogger(__name__)

PSM_SDP = 0x0001
SPP_UUID16 = b"\x11\x01"  # Serial Port Profile UUID16 payload bytes

DE_UUID16 = 0x19
DE_UINT16 = 0x09
DE_UINT8 = 0x08
DE_SEQ_1LEN = 0x35

PDU_SERVICE_SEARCH_ATTR_REQ = 0x06
PDU_ERROR_RSP = 0x01


def _attr_request_payload() -> bytes:
    """ServiceSearchAttributeRequest parameter pool."""
    search = bytes([DE_UUID16]) + SPP_UUID16
    attr_ids = bytes([DE_SEQ_1LEN, 0x03, DE_UINT16, 0x00, 0x04])
    return search + struct.pack(">H", 0x0800) + attr_ids + b"\x00"


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("SDP server closed the L2CAP link")
        data.extend(chunk)
    return bytes(data)


def _request(sock: socket.socket, key: int, payload: bytes) -> tuple[int, bytes]:
    """Send one SDP request; return (tid, parameter bytes of the reply)."""
    tid = key & 0xFFFF
    frame = bytes([PDU_SERVICE_SEARCH_ATTR_REQ]) + struct.pack(">H", tid) \
        + struct.pack(">H", len(payload)) + payload
    sock.send(frame)
    while True:
        pdu_id, rid, params = _recv_pdu_raw(sock)
        if pdu_id in (0x01,):
            err = struct.unpack(">H", params[:2])[0]
            raise RuntimeError(f"SDP error response 0x{err:04x}")
        if rid == tid:
            return tid, params
        # reply for an earlier transaction — skip it


def _recv_pdu_raw(sock: socket.socket) -> tuple[int, int, bytes]:
    head = _recv_exact(sock, 5)
    pdu_id = head[0]
    tid = struct.unpack(">H", head[1:3])[0]
    param_len = struct.unpack(">H", head[3:5])[0]
    return pdu_id, tid, _recv_exact(sock, param_len)


# --- data element reader ----------------------------------------------------

def _read_element(buf: bytes, pos: int) -> tuple[object, int]:
    """Parse one data element; returns (value, next_pos).

    Values: ints for numeric/UUID types, lists for SEQ/ALT (nested), bytes
    for strings. Only enough of the grammar to walk SDP records.
    """
    head = buf[pos]
    typ = head >> 3
    size_desc = head & 0x07
    pos += 1
    if typ in (0x06, 0x07):  # sequence / alternative
        count = _short_len(buf, pos, size_desc)
        pos = pos + _hdr_size(size_desc)
        out = []
        end = pos + count
        while pos < end:
            elem, pos = _read_element(buf, pos)
            out.append(elem)
        return out, pos
    if typ == 0x03 or typ == 0x04:  # string / URL
        count = _short_len(buf, pos, size_desc)
        pos += _hdr_size(size_desc)
        return buf[pos:pos + count], pos + count
    int_sizes = {0: 1, 1: 2, 2: 4, 3: 8, 4: 16}
    uuid_sizes = {1: 2, 2: 4, 4: 16}
    if typ in (0x01, 0x02):  # uint / signed
        body = int_sizes.get(size_desc, 1)
        value = int.from_bytes(buf[pos:pos + body], "big", signed=False)
        return value, pos + body
    if typ == 0x03:  # UUID type
        body = uuid_sizes.get(size_desc, 2)
        raw = buf[pos:pos + body]
        return int.from_bytes(raw[-2:], "big"), pos + body
    if typ == 0x05:  # boolean
        return buf[pos], pos + 1
    if typ == 0x00:  # nil
        return None, pos
    raise ValueError(f"unsupported SDP element type {typ} (0x{head:02x})")


def _hdr_size(size_desc: int) -> int:
    return {0: 1, 1: 2, 2: 4}.get(size_desc, 1)


def _short_len(buf: bytes, pos: int, size_desc: int) -> int:
    if size_desc == 0:
        return buf[pos]
    if size_desc == 1:
        return struct.unpack(">H", buf[pos:pos + 2])[0]
    return struct.unpack(">I", buf[pos:pos + 4])[0]


def extract_spp_channel(params: bytes) -> int | None:
    """Pull the RFCOMM channel out of ProtocolDescriptorList(s)."""
    try:
        tree, _end = _read_element(params, 0)
    except (ValueError, IndexError) as exc:
        log.debug("SDP attr parse failed: %s", exc)
        return None
    channels: list[int] = []

    def walk(node: object) -> None:
        if isinstance(node, (bytes, bytearray)):
            return
        if not isinstance(node, list):
            return
        joined = []
        for item in node:
            if isinstance(item, list):
                walk(item)
            else:
                joined.append(item)
        # a protocol descriptor is [uuid16(uint), param...]
        if len(joined) >= 2 and joined[-1] == 3 and \
                any(v == 3 for v in joined):
            # RFCOMM UUID is 3; channel is the following uint
            ints = [v for v in joined if isinstance(v, int)]
            for idx, val in enumerate(ints):
                if val == 3 and idx + 1 < len(ints):
                    channels.append(ints[idx + 1])
            return

    walk(tree)
    log.debug("SDP SPP channels: %s", channels)
    return channels[0] if channels else None


def query_spp_channel(addr: str, timeout: float = 3.0) -> int | None:
    """Return the RFCOMM channel of Service 0x1101 on ``addr`` (None if absent)."""
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET,
                         socket.BTPROTO_L2CAP)
    try:
        sock.settimeout(timeout)
        sock.connect((addr, PSM_SDP))
        payload = _attr_request_payload()
        tid = 0
        attributes: list[bytes] = []
        state = b"\x00"
        while True:
            contiguous = payload[:-1] + bytes([len(state)]) + state[1:]
            if len(state) == 1:
                contiguous = payload
            tid += 1
            _tid, params = _request(sock, tid, contiguous)
            state = b""
            if params:
                count = struct.unpack(">H", params[:2])[0]
                attributes.append(params[2:2 + count])
                rest = params[2 + count:]
                if len(rest) >= 1 and rest[0] > 0:
                    state = bytes(rest)  # server halves continuation chain
                    continue
            break
        return extract_spp_channel(b"".join(attributes))
    except (OSError, ValueError, RuntimeError, ConnectionError) as exc:
        log.debug("SDP query to %s failed: %s", addr, exc)
        return None
    finally:
        try:
            sock.close()
        except OSError:
            pass


if __name__ == "__main__":  # pragma: no cover — quick manual check
    import sys

    target = sys.argv[1]
    print("SPP RFCOMM channel:", query_spp_channel(target))
