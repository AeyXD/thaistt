# -*- coding: utf-8 -*-
"""fixture สื่อจริง — ข้ามแบบ "explicit" เมื่อเครื่องไม่มีเครื่องมือจริง (เช่น CI runner)

ตามสเปก Phase S0: "fixture สื่อจริงสั้น (CI ข้ามแบบ explicit ถ้าไม่มี whisper บน runner)"
ต้องการครบ 3 อย่างจึงรัน: ffmpeg (PATH หรือ env THAISTT_FFMPEG) ·
whisper-cli (PATH หรือ env THAISTT_WHISPER) · โมเดล (env THAISTT_TEST_MODEL หรือ
THAISTT_MODEL) — ไม่ครบจะ skip พร้อมเหตุผลชัดเจน ไม่ใช่ fail

ชุดที่ 2 (คำพูดอังกฤษจริง) ใช้ jfk.wav ที่ whisper.cpp ส่งมาพร้อม homebrew
(/opt/homebrew/share/whisper.cpp/jfk.wav) — มีเครื่องไหนถึงรันเครื่องนั้น
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import thaistt  # noqa: E402

CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("THAISTT_")}

JFK_WAV = "/opt/homebrew/share/whisper.cpp/jfk.wav"


def _detect_tools():
    ffmpeg = os.environ.get("THAISTT_FFMPEG") or shutil.which("ffmpeg")
    whisper = os.environ.get("THAISTT_WHISPER") or shutil.which("whisper-cli")
    model = (
        os.environ.get("THAISTT_TEST_MODEL")
        or os.environ.get("THAISTT_MODEL")
    )
    return ffmpeg, whisper, model if (model and os.path.isfile(model)) else None


FFMPEG, WHISPER, MODEL = _detect_tools()

if not (FFMPEG and WHISPER and MODEL):
    raise unittest.SkipTest(
        "ข้าม (explicit): เครื่องนี้/runner นี้ไม่มี whisper.cpp หรือ ffmpeg หรือโมเดล "
        "ครบ — ตั้ง THAISTT_WHISPER/THAISTT_MODEL(/THAISTT_TEST_MODEL)/THAISTT_FFMPEG "
        "เพื่อรัน fixture จริง (CI สาธารณะไม่ติดตั้งให้)"
    )


class TestWithRealBinaries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="thaistt-real-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, media, extra=None):
        out = os.path.join(self.tmp, "draft.srt")
        argv = [media, "-l", "en", "-o", out,
                "--ffmpeg", FFMPEG, "--whisper", WHISPER, "--model", MODEL] + (extra or [])
        buf = io.StringIO()
        env = dict(CLEAN_ENV)
        with mock.patch.dict(os.environ, env, clear=True), redirect_stderr(buf):
            code = thaistt.main(argv)
        return code, buf.getvalue(), out

    def test_sine_silence_still_succeeds(self):
        """คลื่นนิ่งไม่มีเสียงพูด: ต้องสำเร็จ (exit 0) + ไฟล์ SRT เปล่า/จิ๋ว ไม่ใช่พัง"""
        wav = os.path.join(self.tmp, "sine.wav")
        subprocess.run(
            [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
             "-ar", "16000", "-ac", "1", wav],
            check=True,
        )
        code, err, out = self._run(wav)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(out))

    @unittest.skipUnless(os.path.isfile(JFK_WAV), "ไม่มี jfk.wav บนเครื่องนี้")
    def test_real_english_speech_retimed_draft(self):
        """acceptance หลักของ S0: ถอดคำพูดอังกฤษจริงได้ SRT ร่างผ่านชั้นจัดจังหวะ"""
        code, err, out = self._run(JFK_WAV)
        self.assertEqual(code, 0, err)
        self.assertIn("progress: 100%", err)

        with open(out, "r", encoding="utf-8") as fh:
            content = fh.read()
        segments = thaistt.parse_srt(content)
        self.assertGreaterEqual(len(segments), 1, "ต้องได้อย่างน้อย 1 ซับจาก jfk.wav")
        # จัดจังหวะแล้วต้องอยู่ในกฎทุกท่อน (42 ตัวอักษร / 7 วินาที)
        for seg in segments:
            self.assertLessEqual(len(seg.text), 42,
                                 "เกิน max-chars: %r" % seg.text)
            self.assertLessEqual((seg.end - seg.start) / 1000.0, 7.0 + 1e-9,
                                 "เกิน max-dur: %s" % seg)
        # เลขลำดับเรียงใหม่ 1..n + ไทม์ไลน์เริ่มที่ 0 และไม่ทับกัน
        numbers = [int(b.split("\n")[0]) for b in content.strip().split("\n\n")]
        self.assertEqual(numbers, list(range(1, len(segments) + 1)))
        self.assertEqual(segments[0].start, 0)
        for a, b in zip(segments, segments[1:]):
            self.assertLessEqual(a.end, b.start)


if __name__ == "__main__":
    unittest.main()
