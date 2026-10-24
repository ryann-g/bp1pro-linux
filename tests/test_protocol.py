"""Unit tests for the frame codec (protocol.py) — plain unittest, no pytest."""
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from bp1pro import protocol


class Builders(unittest.TestCase):
    def test_anc_query(self):
        self.assertEqual(protocol.anc_query(), "BA33")

    def test_anc_set_without_level_sends_ff(self):
        self.assertEqual(protocol.anc_set(1), "BA3401FF")
        self.assertEqual(protocol.anc_set(1, protocol.UNSET), "BA3401FF")
        self.assertEqual(protocol.anc_set(1, None), "BA3401FF")

    def test_anc_set_with_level(self):
        self.assertEqual(protocol.anc_set(0, 100), "BA340064")
        self.assertEqual(protocol.anc_set(2, 15), "BA34020F")

    def test_anc_set_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            protocol.anc_set(256)
        with self.assertRaises(ValueError):
            protocol.anc_set(1, 300)

    def test_eq_select(self):
        self.assertEqual(protocol.eq_select(3), "BA3103")
        with self.assertRaises(ValueError):
            protocol.eq_select(-1)

    def test_gesture_query(self):
        self.assertEqual(protocol.gesture_query(0), "BA2100")
        self.assertEqual(protocol.gesture_query(11), "BA210B")

    def test_gesture_set_click_op_map(self):
        self.assertEqual(protocol.gesture_set("single", 1, "left"), "BA220101FF")
        self.assertEqual(protocol.gesture_set("double", 3, "right"), "BA2203FF03")
        self.assertEqual(protocol.gesture_set("triple", 0, "both"), "BA22000000")
        self.assertEqual(protocol.gesture_set("long", 6, "both"), "BA22040606")

    def test_gesture_set_validation(self):
        with self.assertRaises(ValueError):
            protocol.gesture_set("quad", 1, "left")
        with self.assertRaises(ValueError):
            protocol.gesture_set("single", 1, "centre")
        with self.assertRaises(ValueError):
            protocol.gesture_set("single", 256, "left")

    def test_inear_frames(self):
        self.assertEqual(protocol.inear_query(), "BA25")
        self.assertEqual(protocol.inear_set(True), "BA2601")
        self.assertEqual(protocol.inear_set(False), "BA2600")

    def test_mode_frames(self):
        self.assertEqual(protocol.mode_query(), "BA23")
        self.assertEqual(protocol.mode_set(True), "BA2401")
        self.assertEqual(protocol.mode_set(False), "BA2400")

    def test_spatial_frames(self):
        # PanoramicSoundViewModel.java:104 (BA42) / :132 (BA43 + %02X)
        self.assertEqual(protocol.spatial_query(), "BA42")
        self.assertEqual(protocol.spatial_set(0), "BA4300")
        self.assertEqual(protocol.spatial_set(2), "BA4302")
        with self.assertRaises(ValueError):
            protocol.spatial_set(256)

    def test_spatial_mode_table(self):
        self.assertEqual(protocol.SPATIAL_MODES[0], "off (common)")
        self.assertEqual(protocol.SPATIAL_MODES[1], "music")
        self.assertEqual(protocol.SPATIAL_MODES[2], "cinema")
        # game/fixed/tracking exist on other models — labelled as such
        self.assertIn("other models", protocol.SPATIAL_MODES[3])
        self.assertIn("other models", protocol.SPATIAL_MODES[4])
        self.assertIn("other models", protocol.SPATIAL_MODES[5])


class Parsing(unittest.TestCase):
    def test_single_frame(self):
        frames, consumed = protocol.extract_frames(b"\xAA\x33\x01\x00")
        self.assertEqual(frames, ["AA330100"])
        self.assertEqual(consumed, 4)

    def test_garbage_before_marker_is_dropped(self):
        frames, consumed = protocol.extract_frames(b"\x01\x02\xAA\x34\x01")
        self.assertEqual(frames, ["AA3401"])
        self.assertEqual(consumed, 5)

    def test_back_to_back_frames_split_on_marker(self):
        frames, consumed = protocol.extract_frames(b"\xAA\x33\x01\x00\xAA\x35\x01")
        self.assertEqual(frames, ["AA330100", "AA3501"])
        self.assertEqual(consumed, 7)

    def test_partial_known_frame_is_queued(self):
        frames, consumed = protocol.extract_frames(b"\xAA\x33\x01")
        self.assertEqual(frames, [])
        self.assertEqual(consumed, 0)

    def test_partial_then_completion(self):
        frames, consumed = protocol.extract_frames(b"\xAA\x33\x01")
        frames2, consumed2 = protocol.extract_frames(
            b"\xAA\x33\x01" + b"\x00")
        self.assertEqual(frames2, ["AA330100"])
        self.assertEqual(consumed2, 4)

    def test_unknown_trailing_frame_emitted(self):
        frames, consumed = protocol.extract_frames(b"\xAA\x77\x55")
        self.assertEqual(frames, ["AA7755"])
        self.assertEqual(consumed, 3)

    def test_bare_marker_waits_for_type_byte(self):
        frames, consumed = protocol.extract_frames(b"\xAA")
        self.assertEqual(frames, [])
        self.assertEqual(consumed, 0)


class Decoding(unittest.TestCase):
    def test_anc_status(self):
        self.assertEqual(protocol.decode("AA330100"),
                         "ANC status: noise cancelling, strength 0")

    def test_anc_status_unset_level(self):
        self.assertEqual(protocol.decode("AA3301FF"),
                         "ANC status: noise cancelling, strength auto (unset)")

    def test_anc_strength_full_range(self):
        """Live firmware echo verified 2026-10-24 (probe_anc_sweep.py)."""
        self.assertEqual(protocol.decode("AA330163"),
                         "ANC status: noise cancelling, strength 99")
        self.assertEqual(protocol.decode("AA330164"),
                         "ANC status: noise cancelling, strength 100")

    def test_ack_codes(self):
        self.assertEqual(protocol.decode("AA3401"), "ANC ack: ok")
        self.assertEqual(protocol.decode("AA340C"),
                         "ANC ack: rejected: activate Ambient ANC "
                         "(double-connect) first")
        self.assertEqual(protocol.decode("AA340D"),
                         "ANC ack: rejected: cannot switch while in a call")

    def test_eq_and_wear(self):
        self.assertEqual(protocol.decode("AA3002"),
                         "EQ ack: preset 2 selected (preset 2)")
        self.assertEqual(protocol.decode("AA3501"), "wear detected (earbuds put on)")

    def test_unknown_frame_passthrough(self):
        self.assertEqual(protocol.decode("AA1B0200"), "device frame AA1B0200")

    def test_real_device_capture_frames(self):
        """Frames captured live from the BP1 Pro on 2026-10-24 (channel 1)."""
        self.assertEqual(protocol.decode("AA330165"),
                         "ANC status: noise cancelling, preset 1")
        self.assertEqual(protocol.decode("AA0264006401"),
                         "battery: left 100, right 100")
        self.assertEqual(protocol.decode("AA276400"),
                         "charging-case battery 100")
        self.assertEqual(protocol.decode("AA3001"),
                         "EQ ack: preset 1 selected (preset 1)")
        self.assertEqual(protocol.decode("AA2300"),
                         "sound settings reply: 0x00")

    def test_anc_level_byte_value_spaces(self):
        """>=101 preset-id space per NoiseReduceViewModel.r() / PopWindow:980."""
        self.assertEqual(protocol.decode("AA330166"),
                         "ANC status: noise cancelling, preset 2")
        self.assertEqual(protocol.decode("AA330167"),
                         "ANC status: noise cancelling, preset 3")
        self.assertEqual(protocol.decode("AA330168"),
                         "ANC status: noise cancelling, preset id 104")

    def test_real_burst_stream_framing(self):
        """The connect-time burst as one long stream parses into 5 frames."""
        stream = ("AA330165AA0264006401AA276400AA3001AA2300")
        frames, consumed = protocol.extract_frames(bytes.fromhex(stream))
        self.assertEqual(frames, ["AA330165", "AA0264006401",
                                  "AA276400", "AA3001", "AA2300"])
        self.assertEqual(consumed, len(stream) // 2)

    def test_non_device_frame(self):
        self.assertEqual(protocol.decode("BA33"), "(not a device frame)")


class SpatialDecoding(unittest.TestCase):
    """AA42 state / AA43 ack semantics (PanoramicSoundViewModel.x():229,
    EarphoneFunctionShowFragmentNewUI.java:628-711)."""

    def test_spatial_state(self):
        self.assertEqual(protocol.decode("AA4200"), "8D spatial: off")
        self.assertEqual(protocol.decode("AA4201"),
                         "8D spatial: music (state 0x01)")
        self.assertEqual(protocol.decode("AA4202"),
                         "8D spatial: cinema (state 0x02)")

    def test_spatial_state_unknown_mode(self):
        self.assertEqual(protocol.decode("AA4210"),
                         "8D spatial: unknown (state 0x10)")

    def test_spatial_ack_ok_and_failures(self):
        self.assertEqual(protocol.decode("AA4301"), "8D spatial: applied")
        self.assertIn("LHDC", protocol.decode("AA430A"))
        self.assertIn("LDAC", protocol.decode("AA430B"))
        self.assertIn("both earbuds", protocol.decode("AA430C"))
        self.assertIn("call", protocol.decode("AA430D"))

    def test_spatial_ack_state_echoes(self):
        self.assertEqual(protocol.decode("AA4300"),
                         "8D spatial: off (buds answered current state)")
        self.assertEqual(protocol.decode("AA4302"),
                         "8D spatial: cinema (buds answered current state)")

    def test_spatial_frames_in_stream(self):
        """A partial trailing AA42 waits for its mode byte (FRAME_LENGTH)."""
        frames, consumed = protocol.extract_frames(
            b"\xAA\x42\x01\xAA\x43\x01\xAA\x42")
        self.assertEqual(frames, ["AA4201", "AA4301"])
        self.assertEqual(consumed, 6)


class EqNames(unittest.TestCase):
    """Local name map (cloud-side in the official app; nothing on the wire)."""

    def test_eq_label_falls_back(self):
        self.assertEqual(protocol.eq_label(7), "preset 7")
        self.assertEqual(protocol.eq_label(3, {3: "Bass"}), "Bass")
        self.assertEqual(protocol.eq_label(9, {3: "Bass"}), "preset 9")

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "cfg.json"
            self.assertEqual(protocol.load_eq_names(path), {})
            protocol.save_eq_name(2, "Bubble", path)
            protocol.save_eq_name("11", "Deep", path)
            self.assertEqual(protocol.load_eq_names(path),
                             {2: "Bubble", 11: "Deep"})

    def test_save_merges_other_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "cfg.json"
            path.write_text('{"addr": "AA:BB"}')
            protocol.save_eq_name(1, "Chill", path)
            data = json.loads(path.read_text())
            self.assertEqual(data["addr"], "AA:BB")
            self.assertEqual(data[protocol.EQ_NAMES_KEY], {"1": "Chill"})

    def test_load_skips_bad_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "cfg.json"
            path.write_text('{"eq_names": {"z": "x"}, "no_key": 1}')
            self.assertEqual(protocol.load_eq_names(path), {})

    def test_save_over_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "cfg.json"
            path.write_text("not json{")
            protocol.save_eq_name(4, "Vocal", path)
            self.assertEqual(protocol.load_eq_names(path), {4: "Vocal"})

    def test_decode_uses_saved_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "cfg.json"
            protocol.save_eq_name(1, "Rock", path)
            with mock.patch.object(protocol, "CONFIG_PATH", path):
                self.assertEqual(
                    protocol.decode("AA3001"),
                    "EQ ack: preset 1 selected (Rock)")


class EqAckFailures(unittest.TestCase):
    """AA30 carry failure codes in the same byte
    (EarEqDefaultRegulationActivityNewUi.java:880-905)."""

    def test_failure_codes(self):
        self.assertIn("retry", protocol.decode("AA3000").lower())
        self.assertIn("both buds", protocol.decode("AA300C"))
        self.assertIn("call", protocol.decode("AA300D"))

    def test_normal_preset_unchanged(self):
        self.assertEqual(protocol.decode("AA3002"),
                         "EQ ack: preset 2 selected (preset 2)")


class EqCustom(unittest.TestCase):
    """BA31 8-band custom-EQ upload frame (EarEqSelfDefinePresenter.G():155,
    band encoder A()/B(), EqRegulationConstant.DefineSelf.f34059a)."""

    def test_full_golden_frame(self):
        frame = protocol.eq_custom(gains=[3, -3, 0, 0, 0, 0, 2, 5])
        self.assertEqual(frame,
            "BA31" "08"          # opcode + slot id
            "640096000A000100"   # 100 Hz, +3 dB, q 1.0, filter 1
            "C8005A000A000100"   # 200 Hz, -3 dB
            "900178000A000100"   # 400 Hz, 0 dB (0 dB = 0x78)
            "200378000A000100"   # 800 Hz, 0 dB
            "E80378000A000100"   # 1 kHz, 0 dB
            "B80B78000A000100"   # 3 kHz, 0 dB
            "70178C000A000100"   # 6 kHz, +2 dB
            "1027AA000A000100")  # 10 kHz, +5 dB

    def test_zero_gains_default_plan(self):
        frame = protocol.eq_custom(gains=[0.0] * 8)
        self.assertEqual(frame[:6], "BA3108")
        self.assertEqual(frame[6:22], "640078000A000100")  # 100 Hz, 0 dB
        self.assertEqual(frame[-16:], "102778000A000100")  # 10 kHz, 0 dB
        self.assertEqual(len(frame), 6 + 8 * 16)           # 8 bands x 8 B

    def test_alternate_plan_q_and_slot(self):
        frame = protocol.eq_custom(
            gains=[0.0] * 8, freqs=[65, 125, 250, 500, 1000, 2000, 5000, 12000],
            qs=[1.2, 0.8, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0], slot_id=1)
        self.assertEqual(frame[:6], "BA3101")
        self.assertEqual(frame[6:38],
                         "410078000C000100"    # 65 Hz, q 1.2 (0x0C)
                         "7D00780008000100")   # 125 Hz, q 0.8 (0x08)

    def test_filter_and_slot_255(self):
        frame = protocol.eq_custom(gains=[0.0] * 8, filt=2, slot_id=255)
        self.assertEqual(frame[:6], "BA31FF")
        self.assertEqual(frame.count("0200"), 8)  # filter bytes per band

    def test_value_guards(self):
        with self.assertRaises(ValueError):  # wrong band count
            protocol.eq_custom([0.0] * 9)
        with self.assertRaises(ValueError):  # gain out of range
            protocol.eq_custom([-13.0] + [0.0] * 7)
        with self.assertRaises(ValueError):  # freq list wrong length
            protocol.eq_custom([0.0] * 8, freqs=[100] * 7)
        with self.assertRaises(ValueError):  # q list wrong length
            protocol.eq_custom([0.0] * 8, qs=[1.0] * 7)


if __name__ == "__main__":
    unittest.main()
