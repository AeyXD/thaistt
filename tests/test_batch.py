# -*- coding: utf-8 -*-
"""เทสโหมด batch (v0.2) — ทดแทนสคริปต์ชั่วคราว xargs+run.sh ของ ThaiW1Setup

- TestBatchHelpers: unit ผู้ช่วยล้วน (ทุกแพลตฟอร์ม — ไม่ต้องมี whisper/ffmpeg)
- TestBatchCliContract: ทาง error ของ CLI batch (ทุกแพลตฟอร์ม)
- TestBatchWithFakes: integration ท่อเต็มด้วย fake binaries (POSIX — CI Windows ข้าม)
"""
import csv
import io
import os
import shutil
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import thaistt  # noqa: E402

FAKE_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_FFMPEG = FAKE_DIR / "fake_ffmpeg.sh"
FAKE_WHISPER = FAKE_DIR / "fake_whisper.sh"

# แยะ home กันโมเดลจริงของเครื่อง (~/.local/share/whisper-models) หลุดเข้าเทส
ISOLATED_HOME = tempfile.mkdtemp(prefix="thaistt-batchhome-")


def tearDownModule():
    shutil.rmtree(ISOLATED_HOME, ignore_errors=True)


CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("THAISTT_")}
CLEAN_ENV["HOME"] = ISOLATED_HOME
CLEAN_ENV["USERPROFILE"] = ISOLATED_HOME


class TestBatchHelpers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="thaistt-batchunit-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _touch(self, name):
        path = os.path.join(self.tmp, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"x")
        return path

    def test_expand_folder_filters_and_sorts(self):
        folder = os.path.join(self.tmp, "voices")
        os.makedirs(os.path.join(folder, "subdir"), exist_ok=True)
        for name in ("b.ogg", "a.mp3", "notes.say", ".hidden.wav", "readme.txt"):
            with open(os.path.join(folder, name), "wb") as fh:
                fh.write(b"x")
        # ไฟล์ตรง + โฟลเดอร์ + ไฟล์ซ้ำ (dedupe)
        direct = self._touch("direct.wav")
        result = thaistt._expand_batch_inputs([folder, direct, direct])
        self.assertEqual(
            result,
            [os.path.join(folder, "a.mp3"), os.path.join(folder, "b.ogg"), direct],
        )

    def test_expand_missing_raises(self):
        with self.assertRaises(thaistt.ThaisttError) as ctx:
            thaistt._expand_batch_inputs(["/no/such/thing.mp4"])
        self.assertIn("ไม่พบไฟล์/โฟลเดอร์", str(ctx.exception))

    def test_wav_duration(self):
        wav = os.path.join(self.tmp, "d.wav")
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 11200)  # 0.7s
        self.assertAlmostEqual(thaistt._read_wav_duration_seconds(wav), 0.7)
        garbage = os.path.join(self.tmp, "g.wav")
        with open(garbage, "wb") as fh:
            fh.write(b"not a wav")
        self.assertIsNone(thaistt._read_wav_duration_seconds(garbage))

    def test_ghost_phrases(self):
        self.assertTrue(thaistt._looks_ghost("Thank you."))
        self.assertTrue(thaistt._looks_ghost("  thank you.  "))
        self.assertTrue(thaistt._looks_ghost("♪"))
        self.assertFalse(thaistt._looks_ghost("Thank you so much."))

    def test_is_suspicious(self):
        self.assertTrue(thaistt._is_suspicious(0.5, "hello"))          # สั้นเกิน
        self.assertTrue(thaistt._is_suspicious(None, "Thank you."))    # วลีหลอน
        self.assertFalse(thaistt._is_suspicious(None, "hello"))
        self.assertFalse(thaistt._is_suspicious(1.2, "hello there"))

    def test_mp2_is_media(self):
        # W2: เสียง w2speech เป็น mp2 (เคยต้องเปลี่ยนชื่อเป็น .mp3 เอง)
        self.assertTrue(thaistt._is_media_name("voice.mp2"))
        self.assertFalse(thaistt._is_media_name("voice.w2speech"))

    def test_files_from_parser(self):
        listfile = os.path.join(self.tmp, "list.txt")
        one = self._touch("one.ogg")
        two = self._touch("two.mp3")
        with open(listfile, "w", encoding="utf-8") as fh:
            fh.write("# คลิปที่ต้องถอดซ้ำ\n\n%s\n  %s  \n# หมายเหตุ\n" % (one, two))
        self.assertEqual(thaistt._parse_files_from(listfile), [one, two])

    def test_files_from_stdin(self):
        one = self._touch("one.ogg")
        fake_stdin = io.StringIO("# comment\n%s\n\n" % one)
        with mock.patch("sys.stdin", new=fake_stdin):
            self.assertEqual(thaistt._parse_files_from("-"), [one])

    def test_files_from_missing_raises(self):
        with self.assertRaises(thaistt.ThaisttError):
            thaistt._parse_files_from(os.path.join(self.tmp, "nope.txt"))

    def test_prompt_token_warning(self):
        def make_args(prompt="", prompt_file=None):
            return SimpleNamespace(prompt=prompt, prompt_file=prompt_file)

        warnings = []
        say = warnings.append
        # สั้น = เงียบ
        self.assertEqual(thaistt._resolve_prompt(make_args(prompt="Eskel, Geralt"), say),
                         "Eskel, Geralt")
        self.assertEqual(warnings, [])
        # ยาวเกิน ~224 token → เตือน (whisper ตัดส่วนเกินเงียบ ๆ)
        long_prompt = "Name " * 250  # ~1250 ตัวอักษร ≈ 313 token
        thaistt._resolve_prompt(make_args(prompt=long_prompt), say)
        self.assertEqual(len(warnings), 1)
        self.assertIn("token", warnings[0])

    def test_confidence_from_json(self):
        import json as _json
        jpath = os.path.join(self.tmp, "a.wav.json")
        with open(jpath, "w", encoding="utf-8") as fh:
            _json.dump({"transcription": [{"tokens": [
                {"text": "[_BEG_]", "p": 0.2},      # token พิเศษ — ไม่นับ
                {"text": " Hey", "p": 0.2},          # ต่ำ (<0.5)
                {"text": " there", "p": 0.9},
                {"text": " friend.", "p": 0.95},
                {"text": " now", "p": 0.61},
            ]}]}, fh)
        self.assertEqual(thaistt._confidence_from_json(jpath), (0.665, 25))
        # ไม่มีไฟล์ / ไม่มี token ข้อความ → None (build ไม่รองรับ/คลิปเงียบ)
        self.assertIsNone(thaistt._confidence_from_json(os.path.join(self.tmp, "nope.json")))
        silent = os.path.join(self.tmp, "silent.json")
        with open(silent, "w", encoding="utf-8") as fh:
            fh.write('{"transcription":[]}')
        self.assertIsNone(thaistt._confidence_from_json(silent))

    def test_detect_regex(self):
        line = "whisper_full_with_state: auto-detected language: de (p = 0.999347)"
        m = thaistt._DETECT_RE.search(line)
        self.assertEqual((m.group(1), float(m.group(2))), ("de", 0.999347))
        self.assertIsNone(thaistt._DETECT_RE.search("no language here"))


class TestBatchCliContract(unittest.TestCase):
    """ทาง error ของ batch — พังก่อนรัน external binary ใด ๆ (ทุกแพลตฟอร์ม)"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="thaistt-batchcli-")
        self.m1 = os.path.join(self.tmp, "one.ogg")
        self.m2 = os.path.join(self.tmp, "two.wav")
        for p in (self.m1, self.m2, os.path.join(self.tmp, "m.bin")):
            with open(p, "wb") as fh:
                fh.write(b"x")
        self.outdir = os.path.join(self.tmp, "out")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, extra, env_overrides=None):
        env = dict(CLEAN_ENV)
        if env_overrides:
            env.update(env_overrides)
        argv = [self.m1, self.m2, "-l", "en", "-o", self.outdir,
                "--ffmpeg", sys.executable, "--whisper", sys.executable,
                "--model", os.path.join(self.tmp, "m.bin")] + extra
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stderr(buf):
            code = thaistt.main(argv)
        return code, buf.getvalue()

    def test_batch_requires_txt_format(self):
        code, err = self.run_main([])  # default format = srt
        self.assertEqual(code, 2)
        self.assertIn("เฉพาะ --format txt", err)

    def test_batch_rejects_range(self):
        code, err = self.run_main(["--format", "txt", "--range", "1-2"])
        self.assertEqual(code, 2)
        self.assertIn("--range", err)

    def test_batch_size_zero(self):
        code, err = self.run_main(["--format", "txt", "--batch-size", "0"])
        self.assertEqual(code, 2)
        self.assertIn("--batch-size", err)

    def test_parallel_ffmpeg_zero(self):
        code, err = self.run_main(["--format", "txt", "--parallel-ffmpeg", "0"])
        self.assertEqual(code, 2)
        self.assertIn("--parallel-ffmpeg", err)

    def test_prompt_and_prompt_file_conflict(self):
        pf = os.path.join(self.tmp, "names.txt")
        with open(pf, "w", encoding="utf-8") as fh:
            fh.write("Aloy\n")
        code, err = self.run_main(["--format", "txt", "--prompt", "A",
                                   "--prompt-file", pf])
        self.assertEqual(code, 2)
        self.assertIn("ไม่ใช่พร้อมกัน", err)

    def test_prompt_file_missing(self):
        code, err = self.run_main(["--format", "txt",
                                   "--prompt-file", os.path.join(self.tmp, "nope.txt")])
        self.assertEqual(code, 2)
        self.assertIn("--prompt-file", err)

    def test_missing_input_path(self):
        env = dict(CLEAN_ENV)
        argv = [self.m1, "/no/such/clip.mp4", "-l", "en", "-o", self.outdir,
                "--format", "txt"]
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stderr(buf):
            code = thaistt.main(argv)
        self.assertEqual(code, 2)
        self.assertIn("ไม่พบไฟล์/โฟลเดอร์", buf.getvalue())

    def test_empty_folder(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        env = dict(CLEAN_ENV)
        argv = [empty, "-l", "en", "-o", self.outdir, "--format", "txt"]
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stderr(buf):
            code = thaistt.main(argv)
        self.assertEqual(code, 2)
        self.assertIn("ไม่พบไฟล์สื่อ", buf.getvalue())

    def test_output_is_existing_file(self):
        code, err = self.run_main(["--format", "txt", "-o", self.m1])
        self.assertEqual(code, 2)
        self.assertIn("โฟลเดอร์ปลายทาง", err)

    def test_force_rejected_in_single_mode(self):
        env = dict(CLEAN_ENV)
        argv = [self.m1, "-l", "en", "-o", os.path.join(self.tmp, "o.srt"), "--force"]
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stderr(buf):
            code = thaistt.main(argv)
        self.assertEqual(code, 2)
        self.assertIn("--force ใช้กับโหมด batch", buf.getvalue())

    def test_detect_lang_rejected_in_single_mode(self):
        env = dict(CLEAN_ENV)
        argv = [self.m1, "-l", "en", "-o", os.path.join(self.tmp, "o.srt"),
                "--detect-lang"]
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stderr(buf):
            code = thaistt.main(argv)
        self.assertEqual(code, 2)
        self.assertIn("--detect-lang ใช้กับโหมด batch", buf.getvalue())

    def test_multi_lang_rejected_in_single_mode(self):
        env = dict(CLEAN_ENV)
        argv = [self.m1, "-l", "en,pl", "-o", os.path.join(self.tmp, "o.srt")]
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stderr(buf):
            code = thaistt.main(argv)
        self.assertEqual(code, 2)
        self.assertIn("หลายภาษา", buf.getvalue())

    def test_multi_lang_auto_or_duplicate_rejected(self):
        for bad in ("en,auto", "en,en", "en,,pl,pl"):
            code, err = self.run_main(["--format", "txt", "-l", bad])
            self.assertEqual(code, 2, "คาด exit 2 สำหรับ -l %s" % bad)
        self.assertIn("auto", self.run_main(["--format", "txt", "-l", "en,auto"])[1])

    def test_files_from_missing_file(self):
        code, err = self.run_main(
            ["--format", "txt",
             "--files-from", os.path.join(self.tmp, "nope.txt")])
        self.assertEqual(code, 2)
        self.assertIn("--files-from", err)


@unittest.skipUnless(os.name == "posix", "fake binaries เป็น shell script (POSIX เท่านั้น)")
class TestBatchWithFakes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="thaistt-batchit-")
        self.outdir = os.path.join(self.tmp, "out")
        self.model = os.path.join(self.tmp, "m.bin")
        with open(self.model, "wb") as fh:
            fh.write(b"")
        self.log_ff = os.path.join(self.tmp, "ff.args")
        self.log_wh = os.path.join(self.tmp, "wh.args")
        self.env = dict(CLEAN_ENV)
        self.env["THAISTT_TEST_ARGLOG_FF"] = self.log_ff
        self.env["THAISTT_TEST_ARGLOG_WH"] = self.log_wh
        self.media = []
        for name in ("m1.ogg", "m2.wav", "m3.mp3"):
            path = os.path.join(self.tmp, name)
            with open(path, "wb") as fh:
                fh.write(b"FAKEMEDIA")
            self.media.append(path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_batch(self, media, extra=None, env_extra=None):
        argv = list(media) + ["-l", "en", "-o", self.outdir, "--format", "txt",
                              "--ffmpeg", str(FAKE_FFMPEG), "--whisper", str(FAKE_WHISPER),
                              "--model", self.model] + (extra or [])
        env = dict(self.env)
        if env_extra:
            env.update(env_extra)
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stderr(buf):
            code = thaistt.main(argv)
        return code, buf.getvalue()

    def lines(self, path):
        with open(path, "r", encoding="utf-8") as fh:
            return [ln.rstrip("\n") for ln in fh if ln.strip()]

    def whisper_runs(self):
        """[(argv...)] แยกตาม invocation — จำแนกจากบรรทัด --help (probe)"""
        runs, current = [], []
        for ln in self.lines(self.log_wh):
            if ln == "--help" and current:
                runs.append(current)
                current = []
            current.append(ln)
        if current:
            runs.append(current)
        # invocation แรกคือ probe (--help เป็น argv เดียว)
        if runs and runs[0] == ["--help"]:
            runs = runs[1:]
        return runs

    def out(self, stem):
        return os.path.join(self.outdir, stem + ".txt")

    def solo_folder(self, name="solo.ogg"):
        """โฟลเดอร์ที่มีคลิปเดียว — บังคับโหมด batch จริงตาม dispatch (ไฟล์เดี่ยว = single)"""
        folder = os.path.join(self.tmp, "solo")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(b"FAKEMEDIA")
        return folder

    def test_batch_two_files_one_whisper_call(self):
        code, err = self.run_batch(self.media[:2])
        self.assertEqual(code, 0, err)
        for stem in ("m1", "m2"):
            with open(self.out(stem), "r", encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "Hello there friend.\n")
        self.assertIn("สรุป batch: ทั้งหมด 2 · สำเร็จ 2", err)
        self.assertIn("progress: 100%", err)
        # whisper รอบเดียว: probe --help + 1 run ที่มี -f สองตัว
        runs = self.whisper_runs()
        self.assertEqual(len(runs), 1, "batch ต้องเรียก whisper รอบเดียว")
        argv = runs[0]
        f_values = [argv[i + 1] for i, a in enumerate(argv) if a == "-f"]
        self.assertEqual(len(f_values), 2)
        self.assertIn("-otxt", argv)
        self.assertNotIn("-of", argv)
        for v in f_values:
            self.assertTrue(v.endswith(".wav"))  # wav ใน temp
        # ffmpeg ครบสองไฟล์ ที่ 16kHz mono
        ff = self.lines(self.log_ff)
        self.assertEqual(ff.count("-i"), 2)
        self.assertEqual(ff[ff.index("-ar") + 1], "16000")
        # temp ถูกล้าง
        for v in f_values:
            self.assertFalse(os.path.exists(os.path.dirname(v)))

    def test_resume_skips_existing(self):
        code1, _ = self.run_batch(self.media[:2])
        self.assertEqual(code1, 0)
        ff_before = len(self.lines(self.log_ff))
        wh_before = len(self.lines(self.log_wh))
        code2, err2 = self.run_batch(self.media[:2])
        self.assertEqual(code2, 0)
        self.assertIn("ข้ามผลเดิม 2", err2)
        self.assertIn("ข้าม 2", err2)
        # ไม่เรียก external binary เพิ่ม
        self.assertEqual(len(self.lines(self.log_ff)), ff_before)
        self.assertEqual(len(self.lines(self.log_wh)), wh_before)

    def test_folder_filters_non_media_and_hidden(self):
        folder = os.path.join(self.tmp, "voices")
        os.makedirs(folder)
        for name in ("x.ogg", "z.say", ".h.wav"):
            with open(os.path.join(folder, name), "wb") as fh:
                fh.write(b"x")
        code, err = self.run_batch([folder])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(self.out("x")))
        self.assertFalse(os.path.exists(self.out("z")))
        self.assertFalse(os.path.exists(self.out(".h")))
        self.assertIn("สำเร็จ 1", err)

    def test_stem_collision_first_wins(self):
        dup = os.path.join(self.tmp, "m1.mp3")
        with open(dup, "wb") as fh:
            fh.write(b"FAKEMEDIA")
        code, err = self.run_batch([self.media[0], dup])
        self.assertEqual(code, 0)
        self.assertIn("สำเร็จ 1", err)
        self.assertIn("ข้าม 1", err)
        self.assertIn("ชื่อชน", err)

    def test_one_bad_clip_fails_only_that_clip(self):
        code, err = self.run_batch(
            self.media[:2], env_extra={"THAISTT_TEST_FFMPEG_FAILSUBSTR": "m2.wav"})
        self.assertEqual(code, 2)
        self.assertIn("พัง 1", err)
        self.assertTrue(os.path.isfile(self.out("m1")))
        self.assertFalse(os.path.exists(self.out("m2")))

    def test_whisper_total_fail_reports_all_and_cleans_temp(self):
        before = set(Path(tempfile.gettempdir()).glob("thaistt-batch-*"))
        code, err = self.run_batch(
            self.media[:2], env_extra={"THAISTT_TEST_WHISPER_FAIL": "1"})
        self.assertEqual(code, 2)
        self.assertIn("พัง 2", err)
        after = set(Path(tempfile.gettempdir()).glob("thaistt-batch-*"))
        self.assertEqual(after, before, "temp ต้องถูกล้างแม้ whisper พังทุกรอบ")

    def test_batch_size_one_calls_per_file(self):
        code, err = self.run_batch(self.media, ["--batch-size", "1"])
        self.assertEqual(code, 0, err)
        runs = self.whisper_runs()
        self.assertEqual(len(runs), 3)
        for argv in runs:
            self.assertEqual(sum(1 for a in argv if a == "-f"), 1)

    def test_prompt_file_forwarded_joined(self):
        pf = os.path.join(self.tmp, "names.txt")
        with open(pf, "w", encoding="utf-8") as fh:
            fh.write("Aloy\nErend the Brave\n")
        code, err = self.run_batch([self.solo_folder()], ["--prompt-file", pf])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(self.out("solo")))
        argv = self.whisper_runs()[0]
        self.assertIn("--prompt", argv)
        self.assertEqual(argv[argv.index("--prompt") + 1], "Aloy Erend the Brave")

    def test_suspicious_clips_flagged(self):
        code, err = self.run_batch(
            [self.solo_folder()],
            env_extra={"THAISTT_TEST_WHISPER_TEXT": "Thank you.",
                       "THAISTT_TEST_FFMPEG_SHORT": "1"})
        self.assertEqual(code, 0)
        self.assertIn("คลิปน่าสงสัย", err)
        self.assertIn("solo.ogg", err)
        self.assertIn('"Thank you."', err)

    def test_quiet_suppresses_progress_but_keeps_summary(self):
        code, err = self.run_batch([self.solo_folder()], ["--quiet"])
        self.assertEqual(code, 0)
        self.assertNotIn("progress:", err)
        self.assertIn("สรุป batch", err)

    # ---------------- v0.3: manifest / confidence / --force / --files-from ----

    def read_manifest(self, subdir=""):
        path = os.path.join(self.outdir, subdir, thaistt.MANIFEST_NAME)
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))

    def test_manifest_with_confidence(self):
        code, err = self.run_batch(self.media[:2])
        self.assertEqual(code, 0, err)
        rows = {r["id"]: r for r in self.read_manifest()}
        self.assertEqual(set(rows), {"m1", "m2"})
        for r in rows.values():
            self.assertEqual(r["status"], "ok")
            self.assertAlmostEqual(float(r["duration_s"]), 1.0, delta=0.05)
            # fake ส่ง token ข้อความ 4 ตัว (p = 0.2/0.9/0.95/0.61) → avg 0.665 · ต่ำ 25%
            self.assertEqual(r["avg_p"], "0.665")
            self.assertEqual(r["low_conf_pct"], "25")
            self.assertEqual(r["suspicious"], "0")
            self.assertEqual(r["text_chars"], "19")  # "Hello there friend."
        self.assertIn("สรุปรายคลิป", err)

    def test_manifest_marks_suspicious_and_fail(self):
        code, _ = self.run_batch(
            self.media[:2],
            env_extra={"THAISTT_TEST_WHISPER_TEXT": "Thank you.",
                       "THAISTT_TEST_FFMPEG_FAILSUBSTR": "m2.wav"})
        self.assertEqual(code, 2)
        rows = {r["id"]: r for r in self.read_manifest()}
        self.assertEqual(rows["m1"]["status"], "ok")
        self.assertEqual(rows["m1"]["suspicious"], "1")  # วลีหลอน
        self.assertEqual(rows["m2"]["status"], "fail")

    def test_manifest_on_resume_all_skip(self):
        self.run_batch(self.media[:2])
        code, err2 = self.run_batch(self.media[:2])
        self.assertEqual(code, 0)
        rows = self.read_manifest()
        self.assertEqual([r["status"] for r in rows], ["skip", "skip"])

    def test_force_reprocesses_existing(self):
        code1, _ = self.run_batch(self.media[:2])
        self.assertEqual(code1, 0)
        wh_before = len(self.lines(self.log_wh))
        code2, err2 = self.run_batch(self.media[:2], ["--force"])
        self.assertEqual(code2, 0, err2)
        self.assertIn("ข้ามผลเดิม 0", err2)
        self.assertGreater(len(self.lines(self.log_wh)), wh_before,
                           "--force ต้องเรียก whisper ใหม่")
        rows = self.read_manifest()
        self.assertEqual([r["status"] for r in rows], ["ok", "ok"])

    def test_files_from_selects_clips(self):
        listfile = os.path.join(self.tmp, "pick.txt")
        with open(listfile, "w", encoding="utf-8") as fh:
            fh.write("# ถอดซ้ำเฉพาะที่ชื่อเพี้ยน\n%s\n" % self.media[1])
        code, err = self.run_batch([self.media[0]], ["--files-from", listfile])
        self.assertEqual(code, 0, err)
        self.assertIn("สำเร็จ 2", err)  # media 1 + files-from 1
        rows = {r["id"]: r for r in self.read_manifest()}
        self.assertEqual(set(rows), {"m1", "m2"})

    # ---------------- v0.4: --detect-lang (ข้อ 9 ของ W1/W2) ----------------

    def test_detect_lang_match_and_mismatch(self):
        # fake ตอบภาษา en ปกติ → ตรงกับ -l en = mismatch 0
        code, err = self.run_batch(self.media[:2], ["--detect-lang"])
        self.assertEqual(code, 0, err)
        rows = {r["id"]: r for r in self.read_manifest()}
        for r in rows.values():
            self.assertEqual(r["lang"], "en")
            self.assertEqual(r["lang_p"], "0.99")
            self.assertEqual(r["lang_mismatch"], "0")
        # สั่งให้ fake ตอบ "de" → คลิปไม่ใช่ภาษาที่สั่ง = mismatch 1
        code, err = self.run_batch(
            self.media[:2], ["--detect-lang", "--force"],
            env_extra={"THAISTT_TEST_WHISPER_LANG": "de"})
        self.assertEqual(code, 0, err)
        rows = {r["id"]: r for r in self.read_manifest()}
        for r in rows.values():
            self.assertEqual(r["lang"], "de")
            self.assertEqual(r["lang_mismatch"], "1")

    def test_detect_lang_unsupported_build_degrades(self):
        # build ไม่มี -dl → เตือน + คอลัมน์ว่าง แต่งานไม่พัง
        code, err = self.run_batch(
            [self.solo_folder()], ["--detect-lang"],
            env_extra={"THAISTT_TEST_WHISPER_NODL": "1"})
        self.assertEqual(code, 0, err)
        self.assertIn("ไม่มี -dl", err)
        row = self.read_manifest()[0]
        self.assertEqual(row["lang"], "")
        self.assertEqual(row["lang_mismatch"], "")

    # ---------------- v0.5: หลายภาษาในคำสั่งเดียว (ข้อ 7 ของ W2) ----------------

    def test_multi_lang_one_conversion_shared_outputs_split(self):
        code, err = self.run_batch(self.media[:2], ["-l", "en,pl"])
        self.assertEqual(code, 0, err)
        self.assertIn("× 2 ภาษา [en,pl]", err)
        for lang in ("en", "pl"):
            for stem in ("m1", "m2"):
                path = os.path.join(self.outdir, lang, stem + ".txt")
                with open(path, encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), "Hello there friend.\n")
            self.assertEqual({r["id"] for r in self.read_manifest(lang)}, {"m1", "m2"})
        # หัวใจข้อ 7: แปลง WAV ครั้งเดียวแชร์ทุกภาษา (ffmpeg 2 ครั้ง ไม่ใช่ 4)
        ff = self.lines(self.log_ff)
        self.assertEqual(ff.count("-i"), 2)
        # whisper รอบเดียวต่อ (กลุ่ม×ภาษา) = 2 รอบ ภาษาถูกส่งเป็นรายตัว
        runs = self.whisper_runs()
        self.assertEqual(len(runs), 2)
        sent_langs = {argv[argv.index("-l") + 1] for argv in runs}
        self.assertEqual(sent_langs, {"en", "pl"})

    def test_multi_lang_resume_skips_all(self):
        self.run_batch(self.media[:2], ["-l", "en,pl"])
        ff_before = len(self.lines(self.log_ff))
        wh_before = len(self.lines(self.log_wh))
        code, err = self.run_batch(self.media[:2], ["-l", "en,pl"])
        self.assertEqual(code, 0, err)
        self.assertIn("ข้ามผลเดิม 4", err)
        self.assertEqual(len(self.lines(self.log_ff)), ff_before)
        self.assertEqual(len(self.lines(self.log_wh)), wh_before)


if __name__ == "__main__":
    unittest.main()
