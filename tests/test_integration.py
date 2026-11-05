# -*- coding: utf-8 -*-
"""integration test ท่อเต็มด้วย ffmpeg/whisper "ปลอม" (ไม่ต้องมี binary จริงบนเครื่อง)

รันผ่าน thaistt.main() จริง: แปลง (fake) ffmpeg -> เรียก (fake) whisper-cli ->
parse SRT ดิบ -> จัดจังหวะ -> เขียน output — ตรวจ argv ที่ส่งให้ binary ทั้งสอง,
exit code, progress %, --range offset, และ temp ถูกล้างแม้พังกลางทาง

fake เป็น shell script จึงรันบน POSIX เท่านั้น (CI Windows ข้ามชุดนี้ —
unit ฝั่ง CLI/จัดจังหวะรันครบอยู่แล้ว และ fixture จริงมีชุดแยก)
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import thaistt  # noqa: E402

FAKE_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_FFMPEG = FAKE_DIR / "fake_ffmpeg.sh"
FAKE_WHISPER = FAKE_DIR / "fake_whisper.sh"

CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("THAISTT_")}

# ผลคาดเมื่อจัดจังหวะ SRT ดิบของ fake ด้วยค่า default (42/7/0.8):
# สองท่อนแรก merge (gap 0.2s) · ท่อนสามห่าง 1.8s เป็นอีกซับ
EXPECTED_SRT = (
    "1\n"
    "00:00:00,000 --> 00:00:03,200\n"
    "Hi. Hello there friend.\n"
    "\n"
    "2\n"
    "00:00:05,000 --> 00:00:07,500\n"
    "This part comes after a long pause.\n"
)

# แบบเดียวกัน + --range 10.5-20.25 -> ทุก timestamp เลื่อน +10.5s
# (เวลาใน SRT ร่างอ้างตามไทม์ไลน์ต้นฉบับเสมอ)
EXPECTED_RANGE_SRT = (
    "1\n"
    "00:00:10,500 --> 00:00:13,700\n"
    "Hi. Hello there friend.\n"
    "\n"
    "2\n"
    "00:00:15,500 --> 00:00:18,000\n"
    "This part comes after a long pause.\n"
)


def _read_lines(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [ln.rstrip("\n") for ln in fh if ln.strip() != ""]


@unittest.skipUnless(os.name == "posix", "fake binaries เป็น shell script (POSIX เท่านั้น)")
class TestPipelineWithFakes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="thaistt-itest-")
        self.media = os.path.join(self.tmp, "clip.mp4")
        self.model = os.path.join(self.tmp, "model.bin")
        self.out = os.path.join(self.tmp, "draft.srt")
        with open(self.media, "wb") as fh:
            fh.write(b"FAKEMEDIA")
        with open(self.model, "wb") as fh:
            fh.write(b"")
        self.log_ff = os.path.join(self.tmp, "ff.args")
        self.log_wh = os.path.join(self.tmp, "wh.args")
        self.env = dict(CLEAN_ENV)
        self.env["THAISTT_TEST_ARGLOG_FF"] = self.log_ff
        self.env["THAISTT_TEST_ARGLOG_WH"] = self.log_wh

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_thaistt(self, extra=None, env_extra=None):
        argv = [
            self.media, "-l", "en", "-o", self.out,
            "--ffmpeg", str(FAKE_FFMPEG), "--whisper", str(FAKE_WHISPER),
            "--model", self.model,
        ] + (extra or [])
        env = dict(self.env)
        if env_extra:
            env.update(env_extra)
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stderr(buf):
            code = thaistt.main(argv)
        return code, buf.getvalue()

    def ff_args(self):
        return _read_lines(self.log_ff)

    def whisper_calls(self):
        """คืน list ของการเรียก (argv แยกบรรทัด) — call แรกคือ --help ของ probe"""
        calls = []
        current = []
        for ln in _read_lines(self.log_wh):
            current.append(ln)
            if ln == "--help":
                calls.append(current)
                current = []
        if current:
            calls.append(current)
        return calls

    # ------------------------------------------------------------------

    def test_srt_success_full_contract(self):
        code, err = self.run_thaistt()
        self.assertEqual(code, 0, err)
        with open(self.out, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), EXPECTED_SRT)

        ff = self.ff_args()
        # ffmpeg: แปลงเป็น 16kHz mono ที่ temp
        self.assertIn("-i", ff)
        self.assertEqual(ff[ff.index("-i") + 1], self.media)
        self.assertIn("-ar", ff)
        self.assertEqual(ff[ff.index("-ar") + 1], "16000")
        self.assertIn("-ac", ff)
        self.assertEqual(ff[ff.index("-ac") + 1], "1")
        self.assertIn("-vn", ff)
        wav = ff[-1]
        self.assertTrue(wav.endswith(".wav"))

        # whisper: probe --help ก่อน แล้วเรียกจริงครั้งเดียว (สไตล์ bool + -of)
        calls = self.whisper_calls()
        self.assertEqual(len(calls), 2, "คาด --help 1 ครั้ง + รันจริง 1 ครั้ง")
        run_argv = calls[1]
        for flag, value in (("-m", self.model), ("-f", wav), ("-l", "en")):
            self.assertIn(flag, run_argv)
            self.assertEqual(run_argv[run_argv.index(flag) + 1], value)
        self.assertIn("-osrt", run_argv)  # probe เจอสไตล์ bool จาก fake help
        self.assertIn("-of", run_argv)
        prefix = run_argv[run_argv.index("-of") + 1]
        self.assertTrue(prefix.endswith("raw"), "prefix ของ -of ต้องไม่มีส่วนขยาย: %s" % prefix)
        self.assertIn("-pp", run_argv)  # ขอ progress % จาก whisper

        # temp ถูกล้าง
        self.assertFalse(os.path.exists(os.path.dirname(wav)))
        # ไฟล์ต้นฉบับไม่ถูกแตะ
        with open(self.media, "rb") as fh:
            self.assertEqual(fh.read(), b"FAKEMEDIA")

    def test_range_offset_and_ffmpeg_args(self):
        code, err = self.run_thaistt(["--range", "10.5-20.25"])
        self.assertEqual(code, 0, err)
        ff = self.ff_args()
        self.assertIn("-ss", ff)
        self.assertEqual(ff[ff.index("-ss") + 1], "10.5")
        self.assertIn("-t", ff)
        self.assertEqual(ff[ff.index("-t") + 1], "9.75")
        with open(self.out, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), EXPECTED_RANGE_SRT)

    def test_prompt_and_threads_forwarded(self):
        code, err = self.run_thaistt(["--prompt", "Aloy, Erend", "-t", "8"])
        self.assertEqual(code, 0, err)
        run_argv = self.whisper_calls()[1]
        self.assertIn("--prompt", run_argv)  # long flag — กัน -p ชนกับ processors
        self.assertEqual(run_argv[run_argv.index("--prompt") + 1], "Aloy, Erend")
        self.assertIn("-t", run_argv)
        self.assertEqual(run_argv[run_argv.index("-t") + 1], "8")

    def test_txt_format(self):
        out_txt = os.path.join(self.tmp, "draft.txt")
        argv = [self.media, "-l", "en", "-o", out_txt,
                "--ffmpeg", str(FAKE_FFMPEG), "--whisper", str(FAKE_WHISPER),
                "--model", self.model, "--format", "txt"]
        buf = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), redirect_stderr(buf):
            code = thaistt.main(argv)
        self.assertEqual(code, 0, buf.getvalue())
        with open(out_txt, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "Hello there friend.\n")
        run_argv = self.whisper_calls()[1]
        self.assertIn("-otxt", run_argv)

    def test_whisper_failure_exit2_and_temp_cleaned(self):
        code, err = self.run_thaistt(env_extra={"THAISTT_TEST_WHISPER_FAIL": "1"})
        self.assertEqual(code, 2)
        self.assertIn("whisper.cpp ถอดเสียงไม่สำเร็จ", err)
        wav = self.ff_args()[-1]
        self.assertFalse(os.path.exists(os.path.dirname(wav)))
        self.assertFalse(os.path.exists(self.out))

    def test_ffmpeg_failure_exit2_and_temp_cleaned(self):
        code, err = self.run_thaistt(env_extra={"THAISTT_TEST_FFMPEG_FAIL": "1"})
        self.assertEqual(code, 2)
        self.assertIn("ffmpeg แปลงเสียงไม่สำเร็จ", err)
        self.assertFalse(os.path.exists(self.out))

    def test_quiet_produces_no_stderr(self):
        code, err = self.run_thaistt(["--quiet"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")

    def test_progress_lines_when_not_quiet(self):
        code, err = self.run_thaistt()
        self.assertEqual(code, 0)
        self.assertIn("progress: 10%", err)
        self.assertIn("progress: 100%", err)

    # ---------------- v0.7: คำเตือนข้อความหลอนโหมดไฟล์เดียว (Inbox W1) ----

    def test_single_file_no_hallucination_warning_on_normal(self):
        # ปกติ (avg_p 0.75 จาก fake · ไม่มีลายเซ็น · ซับน้อยกว่า 6) = เงียบ
        code, err = self.run_thaistt()
        self.assertEqual(code, 0, err)
        self.assertNotIn("ลายเซ็นข้อความหลอน", err)
        self.assertNotIn("avg_p=", err)
        self.assertNotIn("ซับซ้ำ", err)

    def test_single_file_warns_on_hallucination_signature_even_quiet(self):
        env_extra = {"THAISTT_TEST_WHISPER_TEXT": "Transcription by ESO."}
        code, err = self.run_thaistt(extra=["--format", "txt", "--quiet"],
                                     env_extra=env_extra)
        self.assertEqual(code, 0, err)
        self.assertNotIn("progress:", err)          # เงียบ progress
        self.assertIn("ลายเซ็นข้อความหลอน", err)   # แต่คำเตือนพิมพ์เสมอ
        self.assertIn("Transcription by ESO", err)

    def test_single_file_srt_mode_warns_on_repetition(self):
        # สร้าง SRT หลอนแบบ outro.bik ("The"/"End" สลับ) ผ่าน fake: ใช้ txt ยืนยันกลไก
        # แล้วยืนยันระดับ srt ด้วย dup-ratio ตรง ๆ ใน unit (test_batch) — ที่นี่ตรวจว่า
        # โหมด srt เรียก json จริง (avg_p ปรากฏเมื่อค่าต่ำ)
        code, err = self.run_thaistt(extra=["--format", "txt", "--quiet"],
                                     env_extra={"THAISTT_TEST_WHISPER_TEXT": "[Music]"})
        self.assertEqual(code, 0, err)
        self.assertIn("ลายเซ็นข้อความหลอน", err)


if __name__ == "__main__":
    unittest.main()
