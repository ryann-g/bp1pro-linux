"""Transport tests — mock replies plus a scripted fake RFCOMM socket."""
import socket
import unittest
from typing import Optional

from bp1pro import protocol
from bp1pro.transport import (ChannelClosedError, ChannelUnreachableError,
                              MockTransport, RfcommTransport, TransportError)

ADDR = "AA:BB:CC:DD:EE:FF"


class FakeSocket:
    """Scripted AF_BLUETOOTH stand-in (records, answers, per-channel plan)."""

    def __init__(self, script: dict[int, object]) -> None:
        self.script = script
        # channel -> ""(refused) | "!unreach" | "!busy1reply" | hex | "silent"
        self.sent: list[bytes] = []
        self.connect_addr: Optional[tuple] = None
        self.replies_left = 5
        self.dial_attempts = 0

    def settimeout(self, value: float) -> None:  # noqa: D102
        self.timeout = value

    def connect(self, addr: tuple) -> None:
        self.connect_addr = addr
        plan = self.script.get(addr[1])
        if plan == "":
            raise OSError(111, "Connection refused")  # channel closed
        if plan == "!unreach":
            raise OSError(113, "No route to host")
        if plan == "!busy1reply":
            if self.script.get("_dial", 0) == 0:
                self.script["_dial"] = 1
                raise OSError(16, "Device or resource busy")
            # second attempt lands straight on the link and answers BA33
            self.script[addr[1]] = "AA330100"

    def send(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, size: int) -> bytes:
        frame = self.script.get(self.connect_addr[1])
        if (self.replies_left > 0 and isinstance(frame, str)
                and len(frame) >= 4 and all(c in "0123456789ABCDEF"
                                            for c in frame)):
            self.replies_left -= 1
            return bytes.fromhex(frame)
        raise socket.timeout("timed out")

    def close(self) -> None:
        pass


class ChannelScan(unittest.TestCase):
    def setUp(self):
        import bp1pro.transport as tr
        import types
        real_time = tr.time
        self._real_time = real_time
        self._real_repair = tr.repair_adapter
        tr.time = types.SimpleNamespace(sleep=lambda s: None,
                                        monotonic=real_time.monotonic)
        tr.repair_adapter = lambda *a, **k: True

    def tearDown(self):
        import bp1pro.transport as tr
        tr.time = self._real_time
        tr.repair_adapter = self._real_repair

    def test_channel_1_preferred(self):
        plan = {1: "AA330100"}
        opened: list[FakeSocket] = []

        def make() -> FakeSocket:
            fake = FakeSocket(plan)
            opened.append(fake)
            return fake

        tr = RfcommTransport(ADDR, sock_factory=make)
        tr.connect()
        self.assertEqual(tr.channel, 1)
        self.assertTrue(tr.connected)
        self.assertEqual(opened[0].connect_addr, (ADDR, 1))
        self.assertEqual(bytes.fromhex(protocol.anc_query()),
                         opened[0].sent[0])

    def test_channel_2_after_1_refused_exhausted(self):
        plan = {1: "", 2: "AA330100"}
        opened: list[FakeSocket] = []

        def make() -> FakeSocket:
            fake = FakeSocket(plan)
            opened.append(fake)
            return fake

        tr = RfcommTransport(ADDR, sock_factory=make)
        tr.connect()
        self.assertEqual(tr.channel, 2)
        refused = [s for s in opened if s.connect_addr == (ADDR, 1)]
        self.assertEqual(len(refused), 3)  # BUSY_RETRY budget on channel 1
        self.assertIn((ADDR, 2), [s.connect_addr for s in opened])
        winner = [s for s in opened if s.connect_addr == (ADDR, 2)][0]
        self.assertEqual(bytes.fromhex(protocol.anc_query()), winner.sent[0])

    def test_channel_hint_skips_scan(self):
        plan = {7: "AA330100"}

        def make() -> FakeSocket:
            return FakeSocket(plan)

        tr = RfcommTransport(ADDR, channel=7, sock_factory=make)
        tr.connect()
        self.assertEqual(tr.channel, 7)
        frames = tr.exchange(protocol.anc_query())
        self.assertTrue(any(f.startswith("AA33") for f in frames))

    def test_ebusy_retries_then_links(self):
        plan = {1: "!busy1reply"}

        def make() -> FakeSocket:
            return FakeSocket(plan)

        tr = RfcommTransport(ADDR, sock_factory=make)
        tr.connect()
        self.assertEqual(tr.channel, 1)

    def test_all_channels_refused_hint(self):
        plan = {ch: "" for ch in (1, 2, 3, 8)}

        def make() -> FakeSocket:
            return FakeSocket(plan)

        tr = RfcommTransport(ADDR, sock_factory=make)
        with self.assertRaises(TransportError) as cm:
            tr.connect()
        self.assertIn("Baseus app", str(cm.exception))

    def test_unreachable_aborts_scan(self):
        plan = {ch: "!unreach" for ch in range(1, 11)}
        opened: list[FakeSocket] = []

        def make() -> FakeSocket:
            fake = FakeSocket(plan)
            opened.append(fake)
            return fake

        tr = RfcommTransport(ADDR, sock_factory=make)
        with self.assertRaises(TransportError):
            tr.connect()
        # unreachable stops the scan after channel 1 (plus the repair retry)
        self.assertLessEqual(len(opened), 2)

    def test_silent_channel_is_skipped(self):
        # channel 3 accepts the link but never answers the BA33 probe
        plan = {ch: "" for ch in (1, 2, 3, 8)}
        plan[3] = "silent"
        opened: list[FakeSocket] = []

        def make() -> FakeSocket:
            fake = FakeSocket(plan)
            opened.append(fake)
            return fake

        tr = RfcommTransport(ADDR, sock_factory=make)
        with self.assertRaises(TransportError):
            tr.connect()
        self.assertEqual({s.connect_addr[1] for s in opened}, {1, 2, 3, 8})


class MockTransportBehaviour(unittest.TestCase):
    def test_status_sweep_includes_battery_burst(self):
        """First query answers like a fresh link: ANC + battery + case."""
        mock = MockTransport(ADDR)
        mock.connect()
        frames = mock.exchange(protocol.anc_query())
        self.assertEqual(frames, ["AA330100", "AA0264006401", "AA276400"])
        self.assertIn("BA33", mock.sent)
        # one-shot: a second query gets just the ANC reply, like the buds
        mock2 = MockTransport(ADDR)
        mock2.connect()
        self.assertEqual(mock2.exchange(protocol.anc_query()),
                         ["AA330100", "AA0264006401", "AA276400"])
        self.assertEqual(mock2.exchange(protocol.anc_query()), ["AA330100"])

    def test_anc_set_appends_ack_recorded(self):
        mock = MockTransport(ADDR)
        frames = mock.exchange(protocol.anc_set(2, 80))
        self.assertEqual(frames, ["AA3401"])
        self.assertEqual(bytes(mock.written), bytes.fromhex("BA340250"))

    def test_gesture_set_recorded(self):
        mock = MockTransport(ADDR)
        mock.exchange(protocol.gesture_set("double", 3, "both"))
        self.assertEqual(bytes(mock.written), bytes.fromhex("BA22030303"))
