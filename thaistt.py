#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""thaistt — ถอดเสียง/วิดีโอเป็นซับฉบับร่าง (SRT) เพื่อส่งต่อ pipeline แปลไทยของ TStudio

v0.2 — สเปก: ../THub Studio/docs/spec-vox-whisper.md → "Phase S0" + "v0.2"

สองโหมดใน CLI เดียว:
- ไฟล์เดียว: ffmpeg → WAV 16kHz mono (temp) → whisper.cpp → ชั้นจัดจังหวะซับ
  (merge ≤ max-chars/max-dur · split ตรงช่องว่างเงียบ ≥ min-gap) → SRT/txt ร่าง
- batch หลายไฟล์/โฟลเดอร์ (--format txt): แปลง ffmpeg ขนาน → เรียก whisper-cli
  รอบเดียวต่อชุด (multi -f · default 250/ชุด reuse การโหลดโมเดล ~8s ตามหลักฐาน
  W1: 2,474 คลิป/19 นาที แทนเรียกทีละไฟล์ ≈ 46 ชม.) → txt ต่อไฟล์ + สรุปผล
  · ข้ามไฟล์ที่มีผลลัพธ์แล้ว (resume) · ทนคลิปพังบางส่วน (fallback รายไฟล์)
  · ติดป้าย "คลิปน่าสงสัย" (สั้น < 1s / วลีหลอนที่พบบ่อย) ให้คนตรวจรู้ก่อน

หลักการ: Python stdlib-only · whisper.cpp/ffmpeg เป็น external binary (หาผ่าน
args → env THAISTT_WHISPER/THAISTT_MODEL/THAISTT_FFMPEG → PATH) · temp ทำความ
สะอาดเองแม้รันพังกลางทาง · exit 0 = สำเร็จ · 2 = พัง (stderr ไทย + progress %)

หมายเหตุดีไซน์ (จากการทดสอบกับ whisper.cpp build จริงหลายยุค):
- ธง output มีสองสไตล์: รุ่นใหม่แบบ `-osrt <FNAME>` กับรุ่น `-of <prefix>` +
  `-osrt` (bool) — เรา probe จาก `--help` แล้วเลือกเอง พร้อม fallback อีกสไตล์
- batch ใช้สไตล์ bool ล้วน: multi `-f` + `-otxt` (ไม่มี -of) → build ที่รองรับ
  เขียน `<ไฟล์>.wav.txt` ข้าง input ทุกตัว (ยืนยันด้วย build จริง) · สไตล์
  FNAME map ต่อไฟล์ไม่ได้ → เฟลอแบ็กเป็นเรียกรายไฟล์แทน
- บาง build `-p` คือ processors ไม่ใช่ prompt → ใช้ long flag `--prompt` เท่านั้น
- เมื่อสไตล์อาร์กิวเมนต์ผิด บาง build เขียนไฟล์ข้างทางที่ CWD → เราจึงรัน
  whisper โดยให้ cwd = temp dir (path ทุกตัวเป็น absolute) ไฟล์ข้างทางจึงโดน
  ล้างไปพร้อม temp เสมอ
- progress % ของ whisper โหมด multi-input สะสมข้ามไฟล์ (เคยเห็น 1507%) →
  โหมด batch รายงาน progress ระดับชุด ไม่ map ค่าภายใน
"""
import argparse
import concurrent.futures
import dataclasses
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import wave

APP = "thaistt"
VERSION = "0.2.0"

ENV_WHISPER = "THAISTT_WHISPER"
ENV_MODEL = "THAISTT_MODEL"
ENV_FFMPEG = "THAISTT_FFMPEG"

# ค่าจัดจังหวะซับตามสเปก (Netflix-style ที่นำมาใช้กับซับร่าง)
DEFAULT_MAX_CHARS = 42
DEFAULT_MAX_DUR = 7.0
DEFAULT_MIN_GAP = 0.8

# batch ตามหลักฐาน W1 (สเปก v0.2): 250 ไฟล์/ชุด reuse โหลดโมเดล · ffmpeg ขนาน 8
DEFAULT_BATCH_SIZE = 250
DEFAULT_PARALLEL_FFMPEG = 8

# นามสกุลที่รับเมื่อขยายโฟลเดอร์ (W1 voices มี .say ปน — ต้องกรอง)
MEDIA_EXTS = frozenset(
    ".mp4 .mkv .avi .mov .webm .ts .mpg .mpeg .flv .wmv "
    ".wav .ogg .oga .mp3 .m4a .aac .flac .opus .wma".split()
)

# โฟลเดอร์โมเดลตามธรรมเนียมเครื่องเจ้าของ (W1/W2) — ค้นเป็นค่าปริยายเมื่อไม่ได้ระบุ
DEFAULT_MODEL_DIR = "~/.local/share/whisper-models"

# วลีหลอนที่ whisper ชอบผลิตบนเสียงสั้น/เสียงคำราม (หลักฐาน W1: 18/621 บรรทัด)
GHOST_PHRASES = frozenset(
    p.strip().lower()
    for p in ("thank you.", "thanks for watching.", "*gunshot*", "[music]",
              "[applause]", "[laughter]", "♪", "…")
    if p.strip()
)


class ThaisttError(Exception):
    """ข้อผิดพลาดที่รายงานผู้ใช้เป็นภาษาไทยแล้ว (exit 2)"""


@dataclasses.dataclass(frozen=True)
class Segment:
    """ท่อนซับหนึ่งท่อน — เวลาเป็นมิลลิวินาที (ms)"""

    start: int
    end: int
    text: str


# ---------------------------------------------------------------------------
# ชั้น SRT: parse / format
# ---------------------------------------------------------------------------

_TS_LINE = re.compile(
    r"(\d{1,3}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,3}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})"
)


def _ts_to_ms(hours, minutes, seconds, millis):
    return (
        int(hours) * 3600000
        + int(minutes) * 60000
        + int(seconds) * 1000
        + int(millis.ljust(3, "0"))
    )


def _ms_to_ts(ms):
    ms = max(0, int(ms))
    hours, rem = divmod(ms, 3600000)
    minutes, rem = divmod(rem, 60000)
    seconds, millis = divmod(rem, 1000)
    return "%02d:%02d:%02d,%03d" % (hours, minutes, seconds, millis)


def parse_srt(text):
    """แปลง SRT ดิบ (เช่นที่ whisper.cpp เขียน) เป็น list[Segment]

    ยืดหยุ่นพอสำหรับงานร่าง: ทน BOM/CRLF/LF · ข้อความหลายบรรทัดรวมเป็นบรรทัด
    เดียวด้วยช่องว่าง · whitespace ย่อเป็นช่องว่างเดี่ยว · block ที่ไม่มี
    timestamp/ไม่มีข้อความข้ามไปเลย (ไม่พังทั้งไฟล์)
    """
    text = (text or "").replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    segments = []
    for block in re.split(r"\n[ \t]*\n", text):
        lines = [ln.strip() for ln in block.split("\n")]
        lines = [ln for ln in lines if ln]
        ts = None
        ts_idx = -1
        for i, ln in enumerate(lines):
            m = _TS_LINE.search(ln)
            if m:
                ts = m
                ts_idx = i
                break
        if ts is None:
            continue
        body = " ".join(" ".join(lines[ts_idx + 1:]).split())
        if not body:
            continue
        g = ts.groups()
        segments.append(Segment(_ts_to_ms(*g[0:4]), _ts_to_ms(*g[4:8]), body))
    return segments


def format_srt(segments):
    """เรียง SRT พร้อมเลขลำดับใหม่ 1..n (คั่น block ด้วยบรรทัดว่าง · จบด้วย \\n)"""
    blocks = []
    for i, seg in enumerate(segments, 1):
        blocks.append("%d\n%s --> %s\n%s\n" % (i, _ms_to_ts(seg.start), _ms_to_ts(seg.end), seg.text))
    return "\n".join(blocks)


def shift_segments(segments, offset_seconds):
    """เลื่อนไทม์ไลน์ทั้งชุด (ใช้กับ --range ให้เวลาอ้างต้นฉบับ)"""
    if not offset_seconds:
        return list(segments)
    off = int(round(offset_seconds * 1000))
    return [Segment(s.start + off, s.end + off, s.text) for s in segments]


# ---------------------------------------------------------------------------
# ชั้นจัดจังหวะซับ (หัวใจของ v0.1)
# ---------------------------------------------------------------------------

def _split_oversized(seg, max_chars, max_dur_ms):
    """ซอย segment เดี่ยวที่ยาวเกินจำกัด ที่ช่องว่างระหว่างคำที่ใกล้กึ่งกลางที่สุด

    แบ่งเวลาตามสัดส่วนอักขระ (ซับร่าง — คนตรวจแก้ต่ออยู่แล้ว) · คำเดียวยาวเกิน
    หรือซอยแล้วยังเกินคงเดิม (ทำดีกว่านี้ต้องวิเคราะห์เสียงจริง)
    """
    if len(seg.text) <= max_chars and (seg.end - seg.start) <= max_dur_ms:
        return [seg]
    words = seg.text.split()
    if len(words) < 2:
        return [seg]
    total = len(seg.text)
    half = total / 2.0
    best_i, best_dist = 1, None
    offset = 0
    for i in range(1, len(words)):
        offset += len(words[i - 1]) + (1 if i > 1 else 0)
        dist = abs(offset - half)
        if best_dist is None or dist < best_dist:
            best_i, best_dist = i, dist
    left_text = " ".join(words[:best_i])
    right_text = " ".join(words[best_i:])
    frac = len(left_text) / total
    mid = seg.start + int(round((seg.end - seg.start) * frac))
    mid = max(seg.start + 1, min(seg.end - 1, mid))
    left = Segment(seg.start, mid, left_text)
    right = Segment(mid, seg.end, right_text)
    return _split_oversized(left, max_chars, max_dur_ms) + _split_oversized(right, max_chars, max_dur_ms)


def retime_segments(segments, max_chars=DEFAULT_MAX_CHARS, max_dur=DEFAULT_MAX_DUR,
                    min_gap=DEFAULT_MIN_GAP):
    """จัดจังหวะซับร่างให้อ่านไหล:

    1. ซอยท่อนเดี่ยวที่เกิน ``max_chars``/``max_dur`` ที่ช่องว่างระหว่างคำ
    2. merge ท่อนที่ติดกัน (ช่องว่างเงียบ < ``min_gap``) ตราบใดที่ข้อความรวม
       ≤ ``max_chars`` และช่วงเวลารวม ≤ ``max_dur`` — ช่องว่าง ≥ ``min_gap``
       คือขอบเขตซับคนละท่อน ห้าม merge ข้าม
    3. เลขลำดับเรียงใหม่ตอน format
    """
    max_dur_ms = int(round(max_dur * 1000))
    min_gap_ms = int(round(min_gap * 1000))
    pieces = []
    for seg in segments:
        pieces.extend(_split_oversized(seg, max_chars, max_dur_ms))
    merged = []
    for seg in pieces:
        if merged:
            prev = merged[-1]
            gap_ms = seg.start - prev.end
            joined = prev.text + " " + seg.text
            span_ms = seg.end - prev.start
            if gap_ms < min_gap_ms and len(joined) <= max_chars and span_ms <= max_dur_ms:
                merged[-1] = Segment(prev.start, seg.end, joined)
                continue
        merged.append(seg)
    return merged


# ---------------------------------------------------------------------------
# --range SS.ss-SS.ss
# ---------------------------------------------------------------------------

_RANGE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$")


def parse_range(spec):
    """'10.5-42' → (10.5, 42.0) · ผิดรูปแบบ/สับหัวท้าย → ThaisttError ไทย"""
    m = _RANGE_RE.match(spec or "")
    if not m:
        raise ThaisttError(
            "รูปแบบ --range ไม่ถูกต้อง: %r — ต้องเป็น SS.ss-SS.ss เช่น 10.5-42" % (spec,)
        )
    start, end = float(m.group(1)), float(m.group(2))
    if end <= start:
        raise ThaisttError("--range ค่าท้ายต้องมากกว่าค่าต้น (ได้ %g-%g)" % (start, end))
    return start, end


def _fmt_secs(value):
    """เลขวินาที → สตริงกระชับให้ ffmpeg (10.0 → '10', 9.75 → '9.75')"""
    return "%g" % round(float(value), 3)


# ---------------------------------------------------------------------------
# ค้น external binary / โมเดล
# ---------------------------------------------------------------------------

def _resolve_executable(label, flag, env_key, explicit, env_value, names):
    """args → env → PATH · ระบุมาแล้วใช้ไม่ได้ = แจ้งชัดทันที (ไม่เงียบลง PATH)"""
    for candidate in (explicit, env_value):
        if candidate:
            path = os.path.abspath(os.path.expanduser(candidate))
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
            raise ThaisttError(
                "%s ที่ระบุไม่ใช่ไฟล์ executable ที่ใช้ได้: %s" % (label, path)
            )
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    raise ThaisttError(
        "หา %s ไม่เจอ — ติดตั้งให้อยู่ใน PATH หรือระบุด้วย %s หรือ env %s"
        % (label, flag, env_key)
    )


def _resolve_model(explicit, env_value):
    path = explicit or env_value
    if not path:
        # ค้นโฟลเดอร์ธรรมเนียม (~/.local/share/whisper-models) เป็นค่าปริยาย
        # ใช้เฉพาะกรณีมีไฟล์โมเดล "ไฟล์เดียว" — มีหลายไฟล์ไม่เดา ให้ระบุเอง
        model_dir = os.path.expanduser(DEFAULT_MODEL_DIR)
        if os.path.isdir(model_dir):
            candidates = sorted(
                f for f in os.listdir(model_dir)
                if f.endswith((".bin", ".ggml")) and f != "for-tests-ggml-tiny.bin"
            )
            if len(candidates) == 1:
                return os.path.join(model_dir, candidates[0])
            if len(candidates) > 1:
                raise ThaisttError(
                    "พบโมเดลหลายไฟล์ใน %s ไม่ทราบจะใช้ตัวไหน — ระบุด้วย --model หรือ env %s:\n  %s"
                    % (model_dir, ENV_MODEL, "\n  ".join(candidates))
                )
        raise ThaisttError(
            "ไม่ได้ระบุโมเดล whisper — ใช้ --model หรือ env %s "
            "(ไฟล์ ggml เช่น ggml-base.en.bin)" % ENV_MODEL
        )
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise ThaisttError("ไม่พบไฟล์โมเดล: %s" % path)
    return path


# ---------------------------------------------------------------------------
# subprocess: ffmpeg → whisper.cpp
# ---------------------------------------------------------------------------

def _ffmpeg_argv(ffmpeg_bin, media, wav_path, range_pair=None):
    cmd = [ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error"]
    if range_pair:
        cmd += ["-ss", _fmt_secs(range_pair[0])]
    cmd += ["-i", media]
    if range_pair:
        cmd += ["-t", _fmt_secs(range_pair[1] - range_pair[0])]
    cmd += ["-vn", "-ar", "16000", "-ac", "1", wav_path]
    return cmd


def _run_ffmpeg_one(ffmpeg_bin, media, wav_path, range_pair=None):
    """แปลงคลิปหนึ่งไฟล์ → (สำเร็จ?, ข้อความ error สั้น) — ไม่ raise (ใช้ทั้งโหมดเดียว/batch)"""
    try:
        proc = subprocess.run(
            _ffmpeg_argv(ffmpeg_bin, media, wav_path, range_pair),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, errors="replace",
        )
    except OSError as exc:
        return False, "เรียกไม่ได้: %s" % exc
    if proc.returncode != 0 or not os.path.isfile(wav_path):
        tail = " ".join((proc.stderr or "").strip().splitlines()[-4:])
        return False, "exit %d: %s" % (proc.returncode, tail or "(ไม่มีข้อความ)")
    return True, ""


def _run_ffmpeg(ffmpeg_bin, media, wav_path, range_pair):
    ok, err = _run_ffmpeg_one(ffmpeg_bin, media, wav_path, range_pair)
    if not ok:
        raise ThaisttError("ffmpeg แปลงเสียงไม่สำเร็จ (%s)" % err)


_PROG_RE = re.compile(r"progress\s*=\s*(\d+)\s*%")


def _probe_whisper(whisper_bin):
    """อ่าน --help เพื่อจับสไตล์ธง output และมี --prompt ไหม

    คืน (fname_style, has_prompt) หรือ None เมื่อสอบถามไม่ได้ (จะ fallback เอง)
    """
    try:
        proc = subprocess.run(
            [whisper_bin, "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", timeout=30,
        )
        text = proc.stdout or ""
    except Exception:
        return None
    if "-osrt" not in text and "-otxt" not in text:
        return None
    line = ""
    for ln in text.splitlines():
        if re.search(r"(?<!\S)-osrt\b", ln):
            line = ln
            break
    fname_style = "FNAME" in line
    has_prompt = "--prompt" in text
    return fname_style, has_prompt


def _whisper_argv(whisper_bin, model, wav, lang, out_path, fmt, threads, prompt,
                  fname_style, has_prompt, with_pp):
    cmd = [whisper_bin, "-m", model, "-f", wav, "-l", lang]
    if with_pp:
        cmd += ["-pp"]
    if fname_style:
        # รุ่นที่รับชื่อไฟล์ตรง ๆ
        cmd += (["-osrt", out_path] if fmt == "srt" else ["-otxt", out_path])
    else:
        # รุ่นที่ -osrt เป็น bool และใช้ -of เป็น prefix (ไม่รวมส่วนขยาย)
        prefix = out_path[: -len(".srt")] if fmt == "srt" else out_path[: -len(".txt")]
        cmd += ["-of", prefix, ("-osrt" if fmt == "srt" else "-otxt")]
    if threads:
        cmd += ["-t", str(threads)]
    if prompt and has_prompt:
        cmd += ["--prompt", prompt]
    return cmd


def _spawn_streaming_stderr(cmd, cwd, on_line):
    """รัน cmd · อ่าน stderr ทีละบรรทัด (ส่งต่อ on_line) · คืน (exit, tail 10 บรรทัด)"""
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, errors="replace",
    )
    lines = []

    def pump():
        try:
            for line in proc.stderr:
                lines.append(line.rstrip("\n"))
                if on_line:
                    on_line(line)
        except Exception:
            pass

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    try:
        returncode = proc.wait()
    finally:
        thread.join(timeout=5)
        try:
            proc.stderr.close()
        except Exception:
            pass
    return returncode, "\n".join(lines[-10:])


def _run_whisper(whisper_bin, model, wav, lang, out_path, fmt, threads, prompt,
                 on_whisper_pct, say):
    """เรียก whisper.cpp ให้ได้ไฟล์ผลลัพธ์ที่ out_path แน่นอน

    เลือกสไตล์ธงจากการ probe + ลอง fallback อีกสไตล์/ตัด -pp ตามลำดับ —
    รันโดย cwd = ไดเรกทอรีของ out_path (temp) ไฟล์ข้างทางจาก build ที่ตั้งชื่อ
    ตาม input จึงตกอยู่ใน temp และถูกล้างไปด้วยเสมอ
    """
    workdir = os.path.dirname(out_path) or "."
    probe = _probe_whisper(whisper_bin)
    if probe is None:
        fname_style, has_prompt = True, True
        style_order = [True, False]
    else:
        fname_style, has_prompt = probe
        style_order = [fname_style, not fname_style]
    if prompt and not has_prompt:
        say("คำเตือน: build ของ whisper.cpp นี้ไม่มีตัวเลือก --prompt — ข้ามคำใบ้ที่ให้มา")

    attempts = [(style, True) for style in style_order]
    attempts.append((style_order[0], False))  # build โบราณไม่มี -pp
    last_rc, last_tail = -1, ""
    for style, with_pp in attempts:
        cmd = _whisper_argv(
            whisper_bin, model, wav, lang, out_path, fmt, threads, prompt,
            style, has_prompt, with_pp,
        )
        def on_line(line, _map=on_whisper_pct):
            m = _PROG_RE.search(line)
            if m and _map:
                _map(int(m.group(1)))
        try:
            rc, tail = _spawn_streaming_stderr(cmd, workdir, on_line)
        except OSError as exc:
            raise ThaisttError("เรียก whisper.cpp ไม่ได้: %s" % exc)
        if rc == 0 and os.path.isfile(out_path):
            return
        last_rc, last_tail = rc, tail
    raise ThaisttError(
        "whisper.cpp ถอดเสียงไม่สำเร็จ (exit %d):\n%s"
        % (last_rc, last_tail.strip() or "(ไม่มีข้อความจาก whisper)")
    )


def _run_whisper_multi(whisper_bin, model, wav_paths, lang, threads, prompt, workdir):
    """เรียก whisper.cpp รอบเดียวด้วยหลายไฟล์ — หัวใจของโหมด batch (reuse โหลดโมเดล)

    ใช้สไตล์ bool ล้วน: `-f w1 -f w2 … -otxt` (ไม่มี -of) → build ที่รองรับ multi-input
    (ยืนยันกับ build จริง) เขียน `<wav>.txt` ข้าง input ทุกตัว · สไตล์ FNAME map ผล
    ต่อไฟล์ไม่ได้ → คืน not-ok เพื่อให้ผู้เรียกเฟลอแบ็กเป็นรายไฟล์แทน
    คืน (ผ่านทั้งชุด?, {wav: มีไฟล์ผลแล้วหรือยัง})
    """
    probe = _probe_whisper(whisper_bin)
    fname_style = probe[0] if probe else False
    if fname_style:
        return False, {w: os.path.isfile(w + ".txt") for w in wav_paths}
    cmd = [whisper_bin, "-m", model]
    for wav in wav_paths:
        cmd += ["-f", wav]
    cmd += ["-l", lang, "-pp", "-otxt"]
    if threads:
        cmd += ["-t", str(threads)]
    if prompt:
        cmd += ["--prompt", prompt]
    try:
        rc, _tail = _spawn_streaming_stderr(cmd, workdir, None)
    except OSError as exc:
        raise ThaisttError("เรียก whisper.cpp ไม่ได้: %s" % exc)
    done = {wav: os.path.isfile(wav + ".txt") for wav in wav_paths}
    return (rc == 0 and all(done.values())), done


# ---------------------------------------------------------------------------
# pipeline + CLI
# ---------------------------------------------------------------------------

def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        raise ThaisttError("อ่านไฟล์ผลลัพธ์ %s ไม่ได้: %s" % (path, exc))


def _write_text(path, content):
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
    except OSError as exc:
        raise ThaisttError("เขียนไฟล์ %s ไม่ได้: %s" % (path, exc))


def _resolve_prompt(args):
    """--prompt ตรง ๆ หรือ --prompt-file (รายชื่อตัวละคร/ศัพท์เฉพาะ ตามข้อเสนอ W1)
    → ข้อความ prompt สำหรับ whisper (long flag --prompt)"""
    if args.prompt and args.prompt_file:
        raise ThaisttError("ใช้ --prompt หรือ --prompt-file อย่างใดอย่างหนึ่ง ไม่ใช่พร้อมกัน")
    if args.prompt_file:
        path = os.path.abspath(os.path.expanduser(args.prompt_file))
        if not os.path.isfile(path):
            raise ThaisttError("ไม่พบไฟล์ --prompt-file: %s" % path)
        joined = " ".join(_read_text(path).split())
        if not joined:
            raise ThaisttError("ไฟล์ --prompt-file ว่างเปล่า: %s" % path)
        return joined
    return args.prompt or ""


def run_pipeline(args, say):
    """ห่วงหลัก 0 → 100% · คืน exit code (0/2) · ThaisttError โยนขึ้นให้ main จัด"""
    # ตรวจ --range ก่อน (ถูกสุด ผิดชี้ทันที)
    range_pair = parse_range(args.range) if args.range else None
    prompt = _resolve_prompt(args)

    if args.max_chars < 1:
        raise ThaisttError("--max-chars ต้อง ≥ 1 (ได้ %d)" % args.max_chars)
    if args.max_dur <= 0:
        raise ThaisttError("--max-dur ต้องเป็นบวก (ได้ %g)" % args.max_dur)
    if args.min_gap < 0:
        raise ThaisttError("--min-gap ต้อง ≥ 0 (ได้ %g)" % args.min_gap)
    if args.threads is not None and args.threads < 1:
        raise ThaisttError("-t/--threads ต้อง ≥ 1 (ได้ %d)" % args.threads)

    # โหมดไฟล์เดียว: media มีสมาชิกเดียวและไม่ใช่โฟลเดอร์ (dispatch ที่ main)
    media = os.path.abspath(os.path.expanduser(args.media[0]))
    if not os.path.isfile(media):
        raise ThaisttError("ไม่พบไฟล์สื่อ: %s" % media)
    out_path = os.path.abspath(os.path.expanduser(args.output))
    if out_path == media:
        raise ThaisttError("ห้ามเขียนทับไฟล์สื่อต้นฉบับ — เลือกชื่อ output อื่น")
    if os.path.isdir(out_path):
        raise ThaisttError(
            "-o ของโหมดไฟล์เดียวต้องเป็น 'ชื่อไฟล์' แต่ที่ให้มาเป็นโฟลเดอร์: %s "
            "(โหมด batch: ใส่สื่อหลายไฟล์ หรือโฟลเดอร์สื่อ)" % out_path)
    out_parent = os.path.dirname(out_path)
    if out_parent and not os.path.isdir(out_parent):
        raise ThaisttError("โฟลเดอร์ปลายทางไม่มีอยู่: %s" % out_parent)

    ffmpeg_bin = _resolve_executable(
        "ffmpeg", "--ffmpeg", ENV_FFMPEG, args.ffmpeg,
        os.environ.get(ENV_FFMPEG), ["ffmpeg"],
    )
    whisper_bin = _resolve_executable(
        "whisper.cpp (whisper-cli)", "--whisper", ENV_WHISPER, args.whisper,
        os.environ.get(ENV_WHISPER), ["whisper-cli"],
    )
    model = _resolve_model(args.model, os.environ.get(ENV_MODEL))

    say("progress: 10% กำลังแปลงเสียงเป็น WAV 16kHz mono ด้วย ffmpeg…")

    # temp ทั้งหมดอยู่ใน context เดียว — พังกลางทางก็ถูกล้างเสมอ
    with tempfile.TemporaryDirectory(prefix="thaistt-") as tmpdir:
        wav_path = os.path.join(tmpdir, "audio16k.wav")
        _run_ffmpeg(ffmpeg_bin, media, wav_path, range_pair)

        say("progress: 25% แปลงเสียงเสร็จ — กำลังถอดเสียงด้วย whisper.cpp…")

        def on_whisper_pct(pct):
            say("progress: %d%% ถอดเสียงด้วย whisper.cpp…" % (25 + pct * 70 // 100))

        if args.format == "srt":
            raw_path = os.path.join(tmpdir, "raw.srt")
            _run_whisper(
                whisper_bin, model, wav_path, args.lang, raw_path, "srt",
                args.threads, prompt, on_whisper_pct, say,
            )
            say("progress: 95% กำลังจัดจังหวะซับ (merge/split)…")
            segments = parse_srt(_read_text(raw_path))
            if range_pair:
                segments = shift_segments(segments, range_pair[0])
            result = retime_segments(segments, args.max_chars, args.max_dur, args.min_gap)
            _write_text(out_path, format_srt(result))
            if not result:
                say("คำเตือน: ไม่พบเสียงพูดในช่วงที่ถอด — เขียน SRT เปล่า")
            say("progress: 100%% เสร็จ — %d ซับ → %s" % (len(result), out_path))
        else:
            raw_path = os.path.join(tmpdir, "raw.txt")
            _run_whisper(
                whisper_bin, model, wav_path, args.lang, raw_path, "txt",
                args.threads, prompt, on_whisper_pct, say,
            )
            content = _read_text(raw_path).strip()
            _write_text(out_path, content + "\n" if content else "")
            if not content:
                say("คำเตือน: ไม่พบเสียงพูดในช่วงที่ถอด — เขียนไฟล์เปล่า")
            say("progress: 100%% เสร็จ — เขียนข้อความ → %s" % out_path)
    return 0


class _ThaiArgumentParser(argparse.ArgumentParser):
    """argparse ที่แจ้งข้อผิดพลาดเป็นไทยและ exit 2 ตาม contract เสมอ"""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "%s: อาร์กิวเมนต์ไม่ถูกต้อง: %s\n(ดูตัวเลือกทั้งหมดด้วย --help)\n"
                    % (APP, message))


def build_parser():
    parser = _ThaiArgumentParser(
        prog=APP,
        description="ถอดเสียง/วิดีโอเป็นซับฉบับร่าง (SRT) — whisper.cpp + ffmpeg รัน local ทั้งหมด",
        epilog="ตัวอย่าง: thaistt clip.mp4 -l en -o clip.draft.srt --range 10.5-42 "
               "--prompt \"Aloy, Erend\" · เวลาใน SRT อ้างตามไทม์ไลน์ไฟล์ต้นฉบับเสมอ",
    )
    parser.add_argument(
        "media", nargs="+", metavar="media|folder",
        help="ไฟล์สื่อที่ ffmpeg รองรับ (mp4/mkv/wav/ogg/mp3/m4a/webm ฯลฯ) — "
             "หลายตัวหรือโฟลเดอร์ = โหมด batch (txt รายคลิป)")
    parser.add_argument(
        "-l", "--lang", dest="lang", default="auto", metavar="lang",
        help="ภาษาพูด เช่น en/ja/zh หรือ auto (default: %(default)s)")
    parser.add_argument(
        "-o", "--output", dest="output", required=True, metavar="draft.srt|outdir",
        help="ไฟล์ปลายทาง (ไฟล์เดียว) หรือโฟลเดอร์ปลายทาง (batch) — ไม่แตะไฟล์ต้นฉบับ")
    parser.add_argument(
        "--whisper", dest="whisper", metavar="path",
        help="path ถึง whisper-cli (default: env %s หรือค้นจาก PATH)" % ENV_WHISPER)
    parser.add_argument(
        "--model", dest="model", metavar="path.ggml",
        help="path โมเดล whisper รูปแบบ ggml (default: env %s)" % ENV_MODEL)
    parser.add_argument(
        "--ffmpeg", dest="ffmpeg", metavar="path",
        help="path ถึง ffmpeg (default: env %s หรือค้นจาก PATH)" % ENV_FFMPEG)
    parser.add_argument(
        "--format", dest="format", choices=["srt", "txt"], default="srt",
        help="srt = ซับร่างมีเวลา (จัดจังหวะแล้ว) · txt = ข้อความล้วนต่อไฟล์ (default: %(default)s)")
    parser.add_argument(
        "--max-chars", dest="max_chars", type=int, default=DEFAULT_MAX_CHARS,
        metavar="N", help="อักขระสูงสุดต่อซับ (merge/split ตามนี้) (default: %(default)s)")
    parser.add_argument(
        "--max-dur", dest="max_dur", type=float, default=DEFAULT_MAX_DUR,
        metavar="SEC", help="ความยาวซับสูงสุดเป็นวินาที (default: %(default)s)")
    parser.add_argument(
        "--min-gap", dest="min_gap", type=float, default=DEFAULT_MIN_GAP,
        metavar="SEC", help="ช่องว่างเงียบ ≥ ค่านี้ (วินาที) = ตัดเป็นซับคนละท่อน (default: %(default)s)")
    parser.add_argument(
        "--range", dest="range", metavar="SS.ss-SS.ss",
        help="ถอดเฉพาะช่วง — เวลาใน SRT ร่างยังอ้างตามไทม์ไลน์ต้นฉบับ")
    parser.add_argument(
        "--prompt", dest="prompt", default="", metavar="TEXT",
        help="คำใบ้ศัพท์เฉพาะ/ชื่อตัวละคร ส่งต่อให้ whisper สะกดให้ถูก")
    parser.add_argument(
        "--prompt-file", dest="prompt_file", metavar="FILE",
        help="คำใบ้จากไฟล์รายชื่อ (เช่นตารางชื่อตัวละครของเกม) — แทนการพิมพ์ยาวในบรรทัดคำสั่ง")
    parser.add_argument(
        "-t", "--threads", dest="threads", type=int, metavar="N",
        help="จำนวน thread ของ whisper.cpp (default: ตาม whisper.cpp)")
    parser.add_argument(
        "--batch-size", dest="batch_size", type=int, default=DEFAULT_BATCH_SIZE,
        metavar="N",
        help="batch: จำนวนไฟล์ต่อการเรียก whisper-cli รอบเดียว — reuse โหลดโมเดล (default: %(default)s ตามหลักฐาน W1)")
    parser.add_argument(
        "--parallel-ffmpeg", dest="parallel_ffmpeg", type=int,
        default=DEFAULT_PARALLEL_FFMPEG, metavar="N",
        help="batch: จำนวน ffmpeg ที่แปลงพร้อมกัน (default: %(default)s)")
    parser.add_argument(
        "--quiet", dest="quiet", action="store_true",
        help="เงียบ — ไม่พิมพ์ progress %% ที่ stderr (ข้อผิดพลาดยังแสดง)")
    return parser


# ---------------------------------------------------------------------------
# โหมด batch รายคลิป (v0.2) — ทดแทนสคริปต์ชั่วคราว xargs+run.sh ของ ThaiW1Setup
# ---------------------------------------------------------------------------

def _is_media_name(name):
    return os.path.splitext(name)[1].lower() in MEDIA_EXTS


def _expand_batch_inputs(paths):
    """'โฟลเดอร์/หลายไฟล์' → รายการไฟล์ absolute (dedupe · กรองนามสกุลในโฟลเดอร์)

    โฟลเดอร์ดูแบบไม่ลึก (W1 voices เป็นแบบแบน) · ไฟล์จุด (ซ่อน) ข้าม
    """
    files, missing = [], []
    for path in paths:
        ap = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(ap):
            files.extend(
                os.path.join(ap, name)
                for name in sorted(os.listdir(ap))
                if not name.startswith(".") and _is_media_name(name)
            )
        elif os.path.isfile(ap):
            files.append(ap)
        else:
            missing.append(path)
    if missing:
        raise ThaisttError("ไม่พบไฟล์/โฟลเดอร์: %s" % ", ".join(missing))
    seen, unique = set(), []
    for f in files:
        real = os.path.realpath(f)
        if real not in seen:
            seen.add(real)
            unique.append(f)
    return unique


def _read_wav_duration_seconds(path):
    """อ่านความยาว WAV ที่เราแปลงเอง (16kHz mono PCM) — อ่านไม่ได้คืน None ไม่พัง"""
    try:
        with wave.open(path, "rb") as wav:
            rate = wav.getframerate() or 1
            return wav.getnframes() / float(rate)
    except Exception:
        return None


def _looks_ghost(text):
    return " ".join(text.lower().split()) in GHOST_PHRASES


def _is_suspicious(duration_s, text):
    """คลิปน่าสงสัย = สั้นกว่า 1 วินาที หรือ ข้อความเป็นวลีหลอนที่พบบ่อย (หลักฐาน W1)"""
    if duration_s is not None and duration_s < 1.0:
        return True
    return _looks_ghost(text)


@dataclasses.dataclass
class _BatchItem:
    src: str
    stem: str
    out_path: str
    idx: int
    wav: str = ""
    duration: object = None
    status: str = ""      # ok | empty | fail | skip
    note: str = ""
    text: str = ""


def _batch_summary(items):
    """สรุปผล (สำเร็จ/ว่าง/พัง/ข้าม) + รายการคลิปน่าสงสัย — พิมพ์ stderr เสมอแม้ --quiet"""
    groups = {"ok": [], "empty": [], "fail": [], "skip": []}
    for it in items:
        groups[it.status].append(it)
    lines = [
        "สรุป batch: ทั้งหมด %d · สำเร็จ %d · ว่าง(ไม่มีเสียงพูด) %d · พัง %d · ข้าม %d"
        % (len(items), len(groups["ok"]), len(groups["empty"]),
           len(groups["fail"]), len(groups["skip"]))
    ]
    for label, group, cap in (("พัง", groups["fail"], 10),
                              ("ข้าม", groups["skip"], 10)):
        for it in group[:cap]:
            lines.append("  %s: %s (%s)" % (label, it.src, it.note or "-"))
        if len(group) > cap:
            lines.append("  %s …อีก %d รายการ" % (label, len(group) - cap))
    suspicious = [
        it for it in groups["ok"] + groups["empty"]
        if _is_suspicious(it.duration, it.text)
    ]
    if suspicious:
        lines.append("คลิปน่าสงสัย (ตรวจก่อน) %d:" % len(suspicious))
        for it in suspicious[:10]:
            dur = "%.1fs" % it.duration if it.duration is not None else "?s"
            snippet = '"%s"' % it.text[:40] if it.text else "(ว่าง)"
            lines.append("  %s (%s) %s" % (os.path.basename(it.src), dur, snippet))
        if len(suspicious) > 10:
            lines.append("  …อีก %d คลิป" % (len(suspicious) - 10))
    print("\n".join(lines), file=sys.stderr, flush=True)
    return len(groups["fail"])


def run_batch(args, say):
    """batch: ขยายรายการ → resume ข้ามของเดิม → แปลงขนาน → ถอดเสียงเป็นชุด → สรุป

    exit 0 = งานครบ (มี 'ว่าง' ได้ — รายงานในสรุป) · มีคลิป 'พัง' หรือพังระดับงาน = 2
    """
    if args.format != "txt":
        raise ThaisttError(
            "โหมด batch รอบนี้รองรับเฉพาะ --format txt "
            "(ซับร่างมีเวลา SRT = รันทีละไฟล์)")
    if args.range:
        raise ThaisttError("--range ใช้กับโหมดไฟล์เดียวเท่านั้น (batch ถอดทั้งคลิปทุกไฟล์)")
    if args.batch_size < 1:
        raise ThaisttError("--batch-size ต้อง ≥ 1 (ได้ %d)" % args.batch_size)
    if args.parallel_ffmpeg < 1:
        raise ThaisttError("--parallel-ffmpeg ต้อง ≥ 1 (ได้ %d)" % args.parallel_ffmpeg)

    prompt = _resolve_prompt(args)
    sources = _expand_batch_inputs(args.media)
    if not sources:
        raise ThaisttError("ไม่พบไฟล์สื่อที่ใช้ได้ในที่ที่ให้มา (ตรวจนามสกุลไฟล์)")

    out_dir = os.path.abspath(os.path.expanduser(args.output))
    if os.path.isfile(out_dir):
        raise ThaisttError("-o ของ batch ต้องเป็นโฟลเดอร์ปลายทาง แต่มีไฟล์อยู่ที่: %s" % out_dir)

    ffmpeg_bin = _resolve_executable(
        "ffmpeg", "--ffmpeg", ENV_FFMPEG, args.ffmpeg,
        os.environ.get(ENV_FFMPEG), ["ffmpeg"],
    )
    whisper_bin = _resolve_executable(
        "whisper.cpp (whisper-cli)", "--whisper", ENV_WHISPER, args.whisper,
        os.environ.get(ENV_WHISPER), ["whisper-cli"],
    )
    model = _resolve_model(args.model, os.environ.get(ENV_MODEL))

    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise ThaisttError("สร้างโฟลเดอร์ปลายทางไม่ได้: %s" % exc)

    # รายการงาน + resume (มีผลลัพธ์แล้ว = ข้าม) + ชื่อ stem ชนกัน
    items, taken = [], {}
    for pos, src in enumerate(sources):
        stem = os.path.splitext(os.path.basename(src))[0]
        out_path = os.path.join(out_dir, stem + ".txt")
        if os.path.exists(out_path):
            items.append(_BatchItem(src, stem, out_path, pos,
                                    status="skip", note="มีผลลัพธ์แล้ว (resume)"))
        elif stem in taken:
            items.append(_BatchItem(src, stem, out_path, pos,
                                    status="skip",
                                    note="ชื่อชนกับ %s — ใช้ของไฟล์แรก" % os.path.basename(taken[stem])))
        else:
            taken[stem] = src
            items.append(_BatchItem(src, stem, out_path, pos))
    todo = [it for it in items if not it.status]
    total = len(todo)
    say("progress: 5%% เริ่ม batch %d ไฟล์ (ข้ามของเดิม %d) — แปลงเสียงขนาน %d งาน…"
        % (len(items), len(items) - total, args.parallel_ffmpeg))

    with tempfile.TemporaryDirectory(prefix="thaistt-batch-") as tmpdir:
        wavs_dir = os.path.join(tmpdir, "wavs")
        os.makedirs(wavs_dir, exist_ok=True)

        lock = threading.Lock()
        progress = {"done": 0, "pct": -1}

        def convert(it):
            wav_path = os.path.join(wavs_dir, "%06d__%s.wav" % (it.idx, it.stem))
            ok, err = _run_ffmpeg_one(ffmpeg_bin, it.src, wav_path)
            with lock:
                if ok:
                    it.wav = wav_path
                    it.duration = _read_wav_duration_seconds(wav_path)
                else:
                    it.status, it.note = "fail", "ffmpeg %s" % err
                progress["done"] += 1
                pct = 5 + 20 * progress["done"] // max(1, total)
                if pct != progress["pct"]:
                    progress["pct"] = pct
                    say("progress: %d%% แปลงเสียง %d/%d…"
                        % (pct, progress["done"], total))

        if todo:
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(args.parallel_ffmpeg, len(todo))) as pool:
                list(pool.map(convert, todo))

        converted = [it for it in todo if not it.status]
        say("progress: 25%% แปลงเสียงเสร็จ %d/%d — ถอดเสียงชุดละ %d ไฟล์ต่อรอบ…"
            % (len(converted), total, args.batch_size))

        batches = [converted[i:i + args.batch_size]
                   for i in range(0, len(converted), args.batch_size)]
        for seq, batch in enumerate(batches, 1):
            wavs = [it.wav for it in batch]
            _ok, done = _run_whisper_multi(
                whisper_bin, model, wavs, args.lang, args.threads, prompt, wavs_dir)
            for it in batch:  # คลิปที่ชุดใหญ่ไม่ได้ผล → เรียกรายไฟล์แยกคลิปพิษ
                if not done.get(it.wav):
                    try:
                        _run_whisper(whisper_bin, model, it.wav, args.lang,
                                     it.wav + ".txt", "txt", args.threads, prompt,
                                     None, say)
                    except ThaisttError as exc:
                        it.status, it.note = "fail", str(exc)
            for it in batch:
                if it.status == "fail":
                    continue
                raw = it.wav + ".txt"
                if os.path.isfile(raw):
                    it.text = _read_text(raw).strip()
                    it.status = "ok" if it.text else "empty"
                else:
                    it.status, it.note = "fail", "whisper ไม่ได้ผลลัพธ์"
            say("progress: %d%% ถอดเสียงชุด %d/%d…"
                % (25 + 70 * seq // max(1, len(batches)), seq, len(batches)))

    # เขียนผลลัพธ์นอก temp — ok/empty เขียนไฟล์เสมอ (ว่าง = ไฟล์เปล่า) ให้ resume สม่ำเสมอ
    for it in items:
        if it.status in ("ok", "empty"):
            _write_text(it.out_path, it.text + "\n" if it.text else "")

    say("progress: 100%% เสร็จ — ผลอยู่ที่ %s" % out_dir)
    fail_count = _batch_summary(items)
    return 2 if fail_count else 0


def _force_utf8_streams():
    """บังคับ stdout/stderr เป็น UTF-8 — กันพังกลางคำบน Windows console

    ที่ codepage เดิม (เช่น cp1252) เขียนข้อความไทยไม่ได้ จะกลายเป็น
    UnicodeEncodeError ตั้งแต่ --help — สำหรับ consumer ที่อ่านทาง pipe
    (THub-Launcher) ก็ได้ UTF-8 สม่ำเสมอทุกแพลตฟอร์ม
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass  # stream ถูกแทนที่ด้วย object ที่ reconfigure ไม่ได้ (เช่น pytest)


def main(argv=None):
    _force_utf8_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.quiet:
        say = lambda _msg: None
    else:
        say = lambda msg: print(msg, file=sys.stderr, flush=True)
    batch_mode = len(args.media) > 1 or any(
        os.path.isdir(os.path.expanduser(p)) for p in args.media
    )
    try:
        return run_batch(args, say) if batch_mode else run_pipeline(args, say)
    except ThaisttError as exc:
        print("%s: %s" % (APP, exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("%s: ถูกยกเลิกกลางทาง (temp ถูกล้างแล้ว)" % APP, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
