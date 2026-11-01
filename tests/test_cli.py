# -*- coding: utf-8 -*-
"""unit test ฝั่ง CLI: argparse ไทย/exit code + ทาง error ทั้งหมด (ไม่ต้องมี whisper)

เรียก thaistt.main() ใน process — ทาง error ที่ไม่ต้องรัน external binary จริง
(ไฟล์/อาร์กิวเมนต์/ค้นเครื่องมือไม่เจอ) ตรวจ exit 2 + ข้อความไทย
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import thaistt  # noqa: E402

# สภาพแวดล้อมสะอาด: ตัด env ของ thaistt ออกทั้งหมดกันค่าที่เครื่องเจ้าของตั้งค้างไว้
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("THAISTT_")}


def run_main(argv, env_overrides=None):
    env = dict(CLEAN_ENV)
    if env_overrides:
        env.update(env_overrides)
    buf = io.StringIO()
    with mock.patch.dict(os.environ, env, clear=True), redirect_stderr(buf):
        code = thaistt.main(argv)
    return code, buf.getvalue()


class TestArgparseContract(unittest.TestCase):
    def test_help_exits_zero(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            with self.assertRaises(SystemExit) as ctx:
                thaistt.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_missing_output_is_exit_2_thai(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            with self.assertRaises(SystemExit) as ctx:
                thaistt.main(["clip.mp4", "-l", "en"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("อาร์กิวเมนต์ไม่ถูกต้อง", buf.getvalue())

    def test_missing_media_is_exit_2(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            with self.assertRaises(SystemExit) as ctx:
                thaistt.main(["-o", "x.srt"])
        self.assertEqual(ctx.exception.code, 2)

    def test_bad_format_choice_is_exit_2(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            with self.assertRaises(SystemExit) as ctx:
                thaistt.main(["clip.mp4", "-l", "en", "-o", "x.srt", "--format", "vtt"])
        self.assertEqual(ctx.exception.code, 2)


class TestErrorPaths(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="thaistt-cli-test-")
        self.media = os.path.join(self.tmp, "clip.mp4")
        with open(self.media, "wb") as fh:
            fh.write(b"FAKEMEDIA")
        self.out = os.path.join(self.tmp, "draft.srt")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_media_not_found(self):
        code, err = run_main(["/definitely/not/here.mp4", "-l", "en", "-o", self.out])
        self.assertEqual(code, 2)
        self.assertIn("ไม่พบไฟล์สื่อ", err)

    def test_refuses_output_same_as_media(self):
        code, err = run_main([self.media, "-l", "en", "-o", self.media])
        self.assertEqual(code, 2)
        self.assertIn("ต้นฉบับ", err)

    def test_output_parent_missing(self):
        code, err = run_main(
            [self.media, "-l", "en", "-o", "/no/such/dir/draft.srt"])
        self.assertEqual(code, 2)
        self.assertIn("โฟลเดอร์ปลายทาง", err)

    def test_ffmpeg_not_found(self):
        env = {k: v for k, v in CLEAN_ENV.items() if k != "PATH"}
        with mock.patch.dict(os.environ, env, clear=True):
            buf = io.StringIO()
            with redirect_stderr(buf):
                code = thaistt.main([self.media, "-l", "en", "-o", self.out])
        self.assertEqual(code, 2)
        self.assertIn("หา ffmpeg ไม่เจอ", buf.getvalue())

    def test_whisper_not_found(self):
        env = {k: v for k, v in CLEAN_ENV.items() if k != "PATH"}
        env["THAISTT_FFMPEG"] = sys.executable
        with mock.patch.dict(os.environ, env, clear=True):
            buf = io.StringIO()
            with redirect_stderr(buf):
                code = thaistt.main([self.media, "-l", "en", "-o", self.out])
        self.assertEqual(code, 2)
        self.assertIn("whisper", buf.getvalue())

    def test_model_not_specified(self):
        code, err = run_main(
            [self.media, "-l", "en", "-o", self.out,
             "--ffmpeg", sys.executable, "--whisper", sys.executable])
        self.assertEqual(code, 2)
        self.assertIn("โมเดล", err)

    def test_model_file_missing(self):
        code, err = run_main(
            [self.media, "-l", "en", "-o", self.out,
             "--ffmpeg", sys.executable, "--whisper", sys.executable,
             "--model", os.path.join(self.tmp, "nope.bin")])
        self.assertEqual(code, 2)
        self.assertIn("ไม่พบไฟล์โมเดล", err)

    @unittest.skipUnless(os.name == "posix",
                         "Windows ไม่มี exec bit — os.access(X_OK) คืน True เสมอ")
    def test_explicit_tool_not_executable(self):
        not_exec = os.path.join(self.tmp, "plain.txt")
        with open(not_exec, "wb") as fh:
            fh.write(b"data")
        code, err = run_main(
            [self.media, "-l", "en", "-o", self.out, "--ffmpeg", not_exec])
        self.assertEqual(code, 2)
        self.assertIn("executable", err)

    def test_bad_range_format(self):
        code, err = run_main(
            [self.media, "-l", "en", "-o", self.out, "--range", "banana"])
        self.assertEqual(code, 2)
        self.assertIn("--range", err)

    def test_bad_range_order(self):
        code, err = run_main(
            [self.media, "-l", "en", "-o", self.out, "--range", "30-10"])
        self.assertEqual(code, 2)
        self.assertIn("--range", err)

    def test_bad_limits(self):
        common = [self.media, "-l", "en", "-o", self.out]
        for extra in (["--max-chars", "0"], ["--max-dur", "0"],
                      ["--min-gap", "-1"], ["-t", "0"]):
            code, err = run_main(common + extra)
            self.assertEqual(code, 2, "คาด exit 2 สำหรับ %s (ได้ stderr: %s)" % (extra, err))
            self.assertIn("thaistt:", err)


if __name__ == "__main__":
    unittest.main()
