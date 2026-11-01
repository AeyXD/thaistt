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
# เสียงพากย์จริงของ Witcher 1 (ใช้จริงกับงาน W1 — มีเครื่องไหนถึงรันเครื่องนั้น)
W1_VOICES = "/Users/prawith/My Workspace/Playgound/ThaiW1Setup/work/voices"


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


class TestBatchReal(unittest.TestCase):
    """batch จริง (v0.2): multi-input whisper รอบเดียว + resume + W1 .ogg จริง"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="thaistt-batchreal-")
        self.outdir = os.path.join(self.tmp, "out")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sine(self):
        wav = os.path.join(self.tmp, "sine.wav")
        subprocess.run(
            [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
             "-ar", "16000", "-ac", "1", wav],
            check=True,
        )
        return wav

    def _run(self, media, extra=None):
        argv = list(media) + ["-l", "en", "-o", self.outdir, "--format", "txt",
                              "--ffmpeg", FFMPEG, "--whisper", WHISPER,
                              "--model", MODEL] + (extra or [])
        buf = io.StringIO()
        env = dict(CLEAN_ENV)
        with mock.patch.dict(os.environ, env, clear=True), redirect_stderr(buf):
            code = thaistt.main(argv)
        return code, buf.getvalue()

    @unittest.skipUnless(os.path.isfile(JFK_WAV), "ไม่มี jfk.wav บนเครื่องนี้")
    def test_batch_jfk_plus_sine_and_resume(self):
        sine = self._sine()
        code, err = self._run([JFK_WAV, sine])
        self.assertEqual(code, 0, err)
        self.assertIn("สรุป batch", err)
        with open(os.path.join(self.outdir, "jfk.txt"), encoding="utf-8") as fh:
            self.assertGreater(len(fh.read().strip()), 0, "jfk ต้องมีข้อความ")
        self.assertTrue(os.path.isfile(os.path.join(self.outdir, "sine.txt")),
                        "คลิปเงียบก็ต้องได้ไฟล์ (ว่าง)")
        # manifest (v0.3): สถานะ/ความยาว/ความมั่นใจต่อคลิป เครื่องอ่านได้
        import csv
        with open(os.path.join(self.outdir, thaistt.MANIFEST_NAME),
                  encoding="utf-8-sig", newline="") as fh:
            rows = {r["id"]: r for r in csv.DictReader(fh)}
        self.assertEqual(rows["jfk"]["status"], "ok")
        self.assertGreater(float(rows["jfk"]["duration_s"]), 10.0)
        self.assertTrue(rows["jfk"]["avg_p"] != "",
                        "build นี้มี -ojf → ต้องมีค่าความมั่นใจต่อคลิป")
        self.assertTrue(0.0 <= float(rows["jfk"]["avg_p"]) <= 1.0)
        self.assertIn(int(rows["jfk"]["low_conf_pct"]), range(0, 101))
        # sine 440Hz ไม่ใช่เงียบสนิท — whisper อาจถอดอะไรออกมา (ok) หรือว่าง (empty)
        self.assertIn(rows["sine"]["status"], ("ok", "empty"))
        # resume: รอบสอง (media ชุดเดิม) ต้องข้ามทั้งคู่และไม่พัง
        code2, err2 = self._run([JFK_WAV, sine])
        self.assertEqual(code2, 0, err2)
        self.assertIn("ข้าม 2", err2)

    @unittest.skipUnless(os.path.isdir(W1_VOICES), "ไม่มีคลังเสียง W1 บนเครื่องนี้")
    def test_batch_w1_real_ogg_clips(self):
        clips = sorted(
            os.path.join(W1_VOICES, f)
            for f in os.listdir(W1_VOICES)
            if f.endswith(".ogg")
        )[:5]
        self.assertGreaterEqual(len(clips), 5)
        code, err = self._run(clips)
        self.assertEqual(code, 0, err)
        non_empty = 0
        for clip in clips:
            out = os.path.join(self.outdir,
                               os.path.splitext(os.path.basename(clip))[0] + ".txt")
            self.assertTrue(os.path.isfile(out), "หาย: %s" % out)
            with open(out, encoding="utf-8") as fh:
                if fh.read().strip():
                    non_empty += 1
        self.assertGreaterEqual(non_empty, 1, "คลิปพากย์จริงต้องมีข้อความบ้าง")


    @unittest.skipUnless(shutil.which("say") and os.path.isfile(JFK_WAV),
                         "ต้องมี say (macOS) + jfk.wav — ทดสอบจับคลิป non-English จริง")
    def test_detect_lang_flags_german_clip(self):
        """ข้อ 9 กับของจริง: คลิปเยอรมัน (TTS) ปนอังกฤษ สั่ง -l en → manifest
        ต้องตรวจพบ de และติด mismatch ให้คลิปเยอรมันเท่านั้น"""
        aiff = os.path.join(self.tmp, "de.aiff")
        subprocess.run(
            ["say", "-v", "Anna", "-o", aiff,
             "Guten Tag, ich heisse Anna und wohne in Berlin."],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        de_wav = os.path.join(self.tmp, "de_clip.wav")
        subprocess.run(
            [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", aiff,
             "-ar", "16000", "-ac", "1", de_wav], check=True)
        code, err = self._run([de_wav, JFK_WAV], extra=["--detect-lang"])
        self.assertEqual(code, 0, err)
        import csv as _csv
        with open(os.path.join(self.outdir, thaistt.MANIFEST_NAME),
                  encoding="utf-8-sig", newline="") as fh:
            rows = {r["id"]: r for r in _csv.DictReader(fh)}
        self.assertEqual(rows["de_clip"]["lang"], "de")
        self.assertEqual(rows["de_clip"]["lang_mismatch"], "1")
        self.assertGreater(float(rows["de_clip"]["lang_p"]), 0.5)
        self.assertEqual(rows["jfk"]["lang"], "en")
        self.assertEqual(rows["jfk"]["lang_mismatch"], "0")


    @unittest.skipUnless(shutil.which("say") and os.path.isfile(JFK_WAV),
                         "ต้องมี say (macOS) + jfk.wav — ทดสอบหลายภาษาจริง")
    def test_multi_lang_en_de_real(self):
        """ข้อ 7 กับของจริง: คลิปเยอรมัน+อังกฤษ สั่ง -l en,de รอบเดียว →
        ผลแยกโฟลเดอร์ · ภาษาถูกของได้ข้อความ (เยอรมันบังคับ de / อังกฤษบังคับ en)"""
        aiff = os.path.join(self.tmp, "de.aiff")
        subprocess.run(
            ["say", "-v", "Anna", "-o", aiff,
             "Guten Tag, ich heisse Anna und wohne in Berlin."],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        de_wav = os.path.join(self.tmp, "de_clip.wav")
        subprocess.run(
            [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", aiff,
             "-ar", "16000", "-ac", "1", de_wav], check=True)
        code, err = self._run([de_wav, JFK_WAV], extra=["-l", "en,de"])
        self.assertEqual(code, 0, err)
        self.assertIn("× 2 ภาษา", err)
        with open(os.path.join(self.outdir, "de", "de_clip.txt"),
                  encoding="utf-8") as fh:
            self.assertGreater(len(fh.read().strip()), 0,
                               "คลิปเยอรมันบังคับ de ต้องได้ข้อความ")
        with open(os.path.join(self.outdir, "en", "jfk.txt"),
                  encoding="utf-8") as fh:
            self.assertGreater(len(fh.read().strip()), 0,
                               "jfk บังคับ en ต้องได้ข้อความ")
        for lang in ("en", "de"):
            self.assertTrue(os.path.isfile(
                os.path.join(self.outdir, lang, thaistt.MANIFEST_NAME)))


if __name__ == "__main__":
    unittest.main()
