# -*- coding: utf-8 -*-
"""unit test ฝั่ง CLI: argparse ไทย/exit code + ทาง error ทั้งหมด (ไม่ต้องมี whisper)

เรียก thaistt.main() ใน process — ทาง error ที่ไม่ต้องรัน external binary จริง
(ไฟล์/อาร์กิวเมนต์/ค้นเครื่องมือไม่เจอ) ตรวจ exit 2 + ข้อความไทย
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

REPO_ROOT = Path(__file__).resolve().parent.parent

# สภาพแวดล้อมสะอาด: ตัด env ของ thaistt ออกทั้งหมดกันค่าที่เครื่องเจ้าของตั้งค้างไว้
# + แยะ home กันโมเดลจริงใน ~/.local/share/whisper-models หลุดเข้าเทส
#   (v0.2 ค้นโฟลเดอร์นี้เป็นค่าปริยายเมื่อไม่ได้ระบุ --model/THAISTT_MODEL)
ISOLATED_HOME = tempfile.mkdtemp(prefix="thaistt-clihome-")


def tearDownModule():
    shutil.rmtree(ISOLATED_HOME, ignore_errors=True)


CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("THAISTT_")}
CLEAN_ENV["HOME"] = ISOLATED_HOME
CLEAN_ENV["USERPROFILE"] = ISOLATED_HOME


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

    def test_no_media_and_no_files_from_is_exit_2(self):
        """ข้อสังเกต W2: --files-from ใช้เดี่ยวได้ → ไม่ใส่ media เลยต้องมี files-from"""
        buf = io.StringIO()
        with redirect_stderr(buf):
            with self.assertRaises(SystemExit) as ctx:
                thaistt.main(["-l", "en", "-o", "x.srt"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("--files-from", buf.getvalue())

    def test_missing_media_is_exit_2(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            with self.assertRaises(SystemExit) as ctx:
                thaistt.main(["-o", "x.srt"])
        self.assertEqual(ctx.exception.code, 2)

    def test_help_survives_legacy_console_encoding(self):
        """--help ไทยต้องไม่ crash แม้ console เป็น encoding เดิม (cp1252)

        regression: GitHub Actions Windows รัน python ด้วย cp1252 — ก่อนมี
        _force_utf8_streams() ตัว --help พังด้วย UnicodeEncodeError
        """
        env = dict(CLEAN_ENV)
        env["PYTHONIOENCODING"] = "cp1252"
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "thaistt.py"), "--help"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(proc.returncode, 0)

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

    def test_model_default_dir_single_candidate(self):
        """ไม่ระบุโมเดล + ~/.local/share/whisper-models มีไฟล์เดียว → ใช้ไฟล์นั้น"""
        home = tempfile.mkdtemp(prefix="thaistt-modelhome-")
        model_dir = os.path.join(home, ".local", "share", "whisper-models")
        os.makedirs(model_dir)
        model_file = os.path.join(model_dir, "ggml-base.en.bin")
        with open(model_file, "wb") as fh:
            fh.write(b"m")
        try:
            # ผ่านการหาโมเดลแล้ว → ไปพังที่ ffmpeg ปลอม (sys.executable) แทน
            code, err = run_main(
                [self.media, "-l", "en", "-o", self.out,
                 "--ffmpeg", sys.executable, "--whisper", sys.executable],
                env_overrides={"HOME": home, "USERPROFILE": home},
            )
            self.assertEqual(code, 2)
            self.assertNotIn("โมเดล", err)
            self.assertIn("ffmpeg แปลงเสียงไม่สำเร็จ", err)
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_model_default_dir_multiple_candidates(self):
        """มีหลายไฟล์ → ไม่เดา แจ้งรายชื่อให้ระบุเอง"""
        home = tempfile.mkdtemp(prefix="thaistt-modelhome-")
        model_dir = os.path.join(home, ".local", "share", "whisper-models")
        os.makedirs(model_dir)
        for name in ("ggml-a.bin", "ggml-b.bin"):
            with open(os.path.join(model_dir, name), "wb") as fh:
                fh.write(b"m")
        try:
            code, err = run_main(
                [self.media, "-l", "en", "-o", self.out,
                 "--ffmpeg", sys.executable, "--whisper", sys.executable],
                env_overrides={"HOME": home, "USERPROFILE": home},
            )
            self.assertEqual(code, 2)
            self.assertIn("พบโมเดลหลายไฟล์", err)
            self.assertIn("ggml-a.bin", err)
        finally:
            shutil.rmtree(home, ignore_errors=True)

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
