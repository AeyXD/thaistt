# -*- coding: utf-8 -*-
"""unit test ชั้นจัดจังหวะซับ — SRT ดิบจำลอง → ผลคาด (ไม่ต้องมี whisper/ffmpeg บนเครื่อง)

ครอบคลุม: parse_srt (CRLF/BOM/หลายบรรทัด/ของเสีย) · format_srt (เลขลำดับใหม่/comma-ms)
· retime_segments (merge ตาม max-chars/max-dur/min-gap · split ท่อนยาว · เวลาเดิมครบ)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import thaistt  # noqa: E402
from thaistt import Segment  # noqa: E402

# SRT ดิบจำลอง "whisper" — สองท่อนแรกติดกัน (gap 0.2s) ต้อง merge ·
# ท่อนสามห่าง 1.8s (≥ min-gap 0.8) ต้องเป็นคนละซับ
RAW_SRT = """1
00:00:00,000 --> 00:00:01,500
Hi.

2
00:00:01,700 --> 00:00:03,200
Hello there friend.

3
00:00:05,000 --> 00:00:07,500
This part comes after a long pause.
"""

EXPECTED_RETIMED_SRT = (
    "1\n"
    "00:00:00,000 --> 00:00:03,200\n"
    "Hi. Hello there friend.\n"
    "\n"
    "2\n"
    "00:00:05,000 --> 00:00:07,500\n"
    "This part comes after a long pause.\n"
)


class TestParseSrt(unittest.TestCase):
    def test_basic_parse(self):
        segs = thaistt.parse_srt(RAW_SRT)
        self.assertEqual(
            segs,
            [
                Segment(0, 1500, "Hi."),
                Segment(1700, 3200, "Hello there friend."),
                Segment(5000, 7500, "This part comes after a long pause."),
            ],
        )

    def test_crlf_bom_multiline_text(self):
        text = (
            "﻿1\r\n"
            "00:00:01,000 --> 00:00:02,000\r\n"
            "Hello\r\n"
            "world\r\n"
            "\r\n"
            "2\r\n"
            "00:00:03,500 --> 00:00:04,000\r\n"
            "Second\r\n"
        )
        segs = thaistt.parse_srt(text)
        self.assertEqual(
            segs,
            [Segment(1000, 2000, "Hello world"), Segment(3500, 4000, "Second")],
        )

    def test_dot_millis_and_leading_space(self):
        # รูปแบบจริงที่ whisper.cpp ชอบเขียน: จุดแทน comma + ช่องว่างนำหน้าข้อความ
        text = "1\n00:00:00.000 --> 00:00:10.400\n And so, my fellow Americans.\n"
        segs = thaistt.parse_srt(text)
        self.assertEqual(segs, [Segment(0, 10400, "And so, my fellow Americans.")])

    def test_skips_malformed_and_empty_blocks(self):
        text = (
            "1\nไม่มีบรรทัดเวลา\nHi\n"
            "\n"
            "2\n00:00:00,000 --> 00:00:01,000\nOK\n"
            "\n"
            "3\n00:00:02,000 --> 00:00:03,000\n"
            "\n"
        )
        segs = thaistt.parse_srt(text)
        self.assertEqual(segs, [Segment(0, 1000, "OK")])

    def test_empty_input(self):
        self.assertEqual(thaistt.parse_srt(""), [])
        self.assertEqual(thaistt.parse_srt(None), [])


class TestFormatSrt(unittest.TestCase):
    def test_golden_with_renumber(self):
        out = thaistt.format_srt(
            [Segment(3661500, 3662500, "ทดสอบไทย"), Segment(7200000, 7201123, "b")]
        )
        self.assertEqual(
            out,
            "1\n01:01:01,500 --> 01:01:02,500\nทดสอบไทย\n"
            "\n"
            "2\n02:00:00,000 --> 02:00:01,123\nb\n",
        )

    def test_ms_to_ts_edges(self):
        self.assertEqual(thaistt._ms_to_ts(0), "00:00:00,000")
        self.assertEqual(thaistt._ms_to_ts(3599999), "00:59:59,999")
        self.assertEqual(thaistt._ms_to_ts(3600000), "01:00:00,000")

    def test_empty(self):
        self.assertEqual(thaistt.format_srt([]), "")


class TestShiftSegments(unittest.TestCase):
    def test_shift_for_range(self):
        segs = [Segment(0, 1500, "Hi.")]
        shifted = thaistt.shift_segments(segs, 10.5)
        self.assertEqual(shifted, [Segment(10500, 12000, "Hi.")])

    def test_zero_offset_is_noop(self):
        segs = [Segment(1, 2, "x")]
        self.assertEqual(thaistt.shift_segments(segs, 0.0), segs)


class TestRetimeMerge(unittest.TestCase):
    def test_golden_merge_and_boundary(self):
        out = thaistt.retime_segments(thaistt.parse_srt(RAW_SRT))
        self.assertEqual(thaistt.format_srt(out), EXPECTED_RETIMED_SRT)

    def test_merge_three_fragments_into_one(self):
        segs = [
            Segment(0, 800, "Hi"),
            Segment(900, 1700, "there"),
            Segment(1800, 2600, "friend"),
        ]
        out = thaistt.retime_segments(segs)
        self.assertEqual(out, [Segment(0, 2600, "Hi there friend")])

    def test_no_merge_when_gap_exactly_min_gap(self):
        segs = [Segment(0, 1000, "A"), Segment(1800, 2800, "B")]  # gap = 800ms
        out = thaistt.retime_segments(segs)
        self.assertEqual(len(out), 2)

    def test_merge_when_gap_just_below_min_gap(self):
        segs = [Segment(0, 1000, "A"), Segment(1799, 2799, "B")]  # gap = 799ms
        out = thaistt.retime_segments(segs)
        self.assertEqual(out, [Segment(0, 2799, "A B")])

    def test_merge_when_chars_exactly_max(self):
        segs = [Segment(0, 1000, "a" * 20), Segment(1100, 2000, "b" * 21)]  # รวม = 42
        out = thaistt.retime_segments(segs)
        self.assertEqual(len(out), 1)

    def test_no_merge_when_chars_exceed_max(self):
        segs = [Segment(0, 1000, "a" * 21), Segment(1100, 2000, "b" * 21)]  # รวม = 43
        out = thaistt.retime_segments(segs)
        self.assertEqual(len(out), 2)

    def test_merge_when_duration_exactly_max(self):
        segs = [Segment(0, 4000, "A"), Segment(4200, 7000, "B")]  # span = 7.0s พอดี
        out = thaistt.retime_segments(segs)
        self.assertEqual(out, [Segment(0, 7000, "A B")])

    def test_no_merge_when_duration_exceeds_max(self):
        segs = [Segment(0, 4000, "A"), Segment(4200, 7001, "B")]  # span = 7.001s
        out = thaistt.retime_segments(segs)
        self.assertEqual(len(out), 2)

    def test_custom_limits_respected(self):
        # ปรับค่าทาง CLI แล้วผลตามค่านั้น: max-chars 5 ห้าม merge "aaa bbb" (7)
        segs = [Segment(0, 1000, "aaa"), Segment(1100, 2000, "bbb")]
        out = thaistt.retime_segments(segs, max_chars=5, max_dur=7.0, min_gap=0.8)
        self.assertEqual(len(out), 2)
        # ปล่อยกว้าง merge ได้
        out = thaistt.retime_segments(segs, max_chars=50, max_dur=7.0, min_gap=5.0)
        self.assertEqual(out, [Segment(0, 2000, "aaa bbb")])


class TestRetimeSplit(unittest.TestCase):
    def test_split_long_segment_by_chars(self):
        text = "And so, my fellow Americans, ask not what your country can do for you."
        self.assertEqual(len(text), 70)
        out = thaistt.retime_segments([Segment(0, 10000, text)])
        self.assertEqual(len(out), 2)
        self.assertTrue(all(len(s.text) <= 42 for s in out))
        self.assertEqual(" ".join(s.text for s in out), text)
        # เวลาเดิมครบทั้งก้อน + ต่อเนื่องกัน
        self.assertEqual(out[0].start, 0)
        self.assertEqual(out[-1].end, 10000)
        self.assertEqual(out[0].end, out[1].start)

    def test_split_long_segment_by_duration(self):
        out = thaistt.retime_segments(
            [Segment(0, 9000, "Hello world foo")], max_chars=100, max_dur=7.0
        )
        self.assertEqual(len(out), 2)
        self.assertTrue(all((s.end - s.start) <= 7000 for s in out))
        self.assertEqual(" ".join(s.text for s in out), "Hello world foo")

    def test_single_oversized_word_untouched(self):
        seg = Segment(0, 3000, "x" * 80)
        out = thaistt.retime_segments([seg])
        self.assertEqual(out, [seg])

    def test_empty(self):
        self.assertEqual(thaistt.retime_segments([]), [])


class TestParseRange(unittest.TestCase):
    def test_valid_ranges(self):
        self.assertEqual(thaistt.parse_range("10.5-20.25"), (10.5, 20.25))
        self.assertEqual(thaistt.parse_range("10-20"), (10.0, 20.0))
        self.assertEqual(thaistt.parse_range("0-1.5"), (0.0, 1.5))

    def test_invalid_format(self):
        for bad in ("banana", "10.5", "1:30-2:00", "10.5-"):
            with self.assertRaises(thaistt.ThaisttError):
                thaistt.parse_range(bad)

    def test_end_before_start(self):
        with self.assertRaises(thaistt.ThaisttError):
            thaistt.parse_range("20-10")


if __name__ == "__main__":
    unittest.main()
