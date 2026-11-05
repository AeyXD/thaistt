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
import csv
import dataclasses
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave

APP = "thaistt"
VERSION = "0.7.0"

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

# นามสกุลที่รับเมื่อขยายโฟลเดอร์ (W1 voices มี .say ปน — ต้องกรอง ·
# .mp2 ตามขอใช้จริงของ W2 · .bik = Bink video ที่ ffmpeg ถอดเสียงได้ในตัว — Inbox 3 พ.ย.)
MEDIA_EXTS = frozenset(
    ".mp4 .mkv .avi .mov .webm .ts .mpg .mpeg .flv .wmv .bik "
    ".wav .ogg .oga .mp3 .mp2 .m4a .aac .flac .opus .wma".split()
)

# ชื่อไฟล์สรุปผลงาน batch ที่ฝั่งเกมอ่านเครื่องได้ (W2: voice_en.py) —
# ขึ้นต้นจุดจึง: ถูกกรองทิ้งเมื่อชี้ outdir เดียวกับโฟลเดอร์สื่อในรอบถัดไป
MANIFEST_NAME = ".thaistt-manifest.csv"

# ขอบตัดประมาณของ --prompt: whisper รับได้ ~n_text_ctx/2 ≈ 224 token (ตัดเกินเงียบ ๆ)
# ประมาณ 4 ตัวอักษร/token (รายชื่ออังกฤษ) → ~896 ตัวอักษร (สอดคล้องเกณฑ์ 900 ที่ W2 ใช้เอง)
PROMPT_TOKEN_LIMIT = 224
PROMPT_CHARS_PER_TOKEN = 4.0

# โฟลเดอร์โมเดลตามธรรมเนียมเครื่องเจ้าของ (W1/W2) — ค้นเป็นค่าปริยายเมื่อไม่ได้ระบุ
DEFAULT_MODEL_DIR = "~/.local/share/whisper-models"

# วลีหลอนที่ whisper ชอบผลิตบนเสียงสั้น/เสียงคำราม (หลักฐาน W1: 18/621 บรรทัด)
GHOST_PHRASES = frozenset(
    p.strip().lower()
    for p in ("thank you.", "thanks for watching.", "*gunshot*", "[music]",
              "[applause]", "[laughter]", "♪", "…")
    if p.strip()
)

# ลายเซ็นข้อความหลอนระดับ "ไฟล์" ของ Whisper บนวิดีโอดนตรี/เสียงประกอบล้วน
# (หลักฐานจริงจาก W1 3 พ.ย.: "Transcription by ESO/CastingWords" · "*Painful music*"
# ซ้ำหลายสิบซับ · "The"/"End" สลับกันทั้งเรื่อง) — ใช้เตือนโหมดไฟล์เดียว (Inbox)
HALLUCINATION_SIGN_RE = re.compile(
    r"(transcription|translation|captions?|subtitles?)\s+by"
    r"|\[\s*music\s*\]"
    r"|\*[^*\n]{0,40}(music|applause)[^*\n]{0,40}\*",
    re.IGNORECASE,
)
# สัดส่วนซับที่เป็นข้อความเดิมซ้ำ ๆ เกินนี้ (ในไฟล์ ≥ 6 ซับ) = น่าสงสัยว่าหลอน
DUPLICATE_RATIO_THRESHOLD = 0.5


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

# token พิเศษของ whisper ([_BEG_] [_EOT_] [_TT_48] …) — ไม่นับเข้าความมั่นใจข้อความ
_SPECIAL_TOKEN_RE = re.compile(r"^\[_.+\]$")

# "main: processing '<path>' (N samples, X sec), ..." — ตัวคั่นอ้างอิง per-file ของ whisper
# (ใช้กับรอบตรวจภาษา -dl ที่ผลออกทาง stderr)
_PROCESSING_RE = re.compile(r"^\s*main: processing '([^']+)'")
# "whisper_full_with_state: auto-detected language: de (p = 0.999347)"
_DETECT_RE = re.compile(r"auto-detected language:\s*(\S+)\s*\(p\s*=\s*([0-9.]+)\)")

# ค่าความมั่นใจต่ำกว่านี้ถือว่า "มั่นใจต่ำ" (manifest: low_conf_pct)
LOW_CONF_THRESHOLD = 0.5


def _confidence_from_json(json_path):
    """อ่าน `<wav>.json` จากธง -ojf ของ whisper.cpp → ความมั่นใจต่อคลิป

    แต่ละ token มี p (probability 0..1) — คำนวณค่าเฉลี่ย + % ของ token ที่มั่นใจต่ำ
    คืน (avg_p, low_conf_pct) หรือ None เมื่อไม่มีไฟล์/parse ไม่ได้/ไม่มี token ข้อความ
    """
    try:
        with open(json_path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
        probs = [
            tok["p"]
            for seg in data.get("transcription", [])
            for tok in seg.get("tokens", [])
            if isinstance(tok.get("p"), (int, float))
            and not _SPECIAL_TOKEN_RE.match(str(tok.get("text", "")))
        ]
    except Exception:
        return None
    if not probs:
        return None
    low = sum(1 for p in probs if p < LOW_CONF_THRESHOLD)
    return round(sum(probs) / len(probs), 3), round(100 * low / len(probs))


def _probe_whisper(whisper_bin):
    """อ่าน --help เพื่อจับสไตล์ธง output · มี --prompt / -ojf / -dl (ตรวจภาษา) ไหม

    คืน (fname_style, has_prompt, has_json, has_detect) หรือ None เมื่อสอบถามไม่ได้
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
    has_json = "--output-json-full" in text
    has_detect = "--detect-language" in text
    return fname_style, has_prompt, has_json, has_detect


def _json_path_for(out_path, fmt):
    """ตำแหน่งไฟล์ -ojf ที่ whisper เขียน: <prefix ไม่รวมส่วนขยาย>.json (ทั้งสองสไตล์)"""
    suffix = ".srt" if fmt == "srt" else ".txt"
    return out_path[: -len(suffix)] + ".json"


def _whisper_argv(whisper_bin, model, wav, lang, out_path, fmt, threads, prompt,
                  fname_style, has_prompt, with_pp, json_out=False):
    cmd = [whisper_bin, "-m", model, "-f", wav, "-l", lang]
    if with_pp:
        cmd += ["-pp"]
    if json_out:
        # json เต็มพร้อม p ต่อ token → ความมั่นใจ + คำเตือนข้อความหลอน
        # สไตล์ FNAME ต้องส่งชื่อไฟล์ตรง ๆ (ธงเปล่า ๆ จะกลืนอาร์กิวเมนต์ถัดไป)
        if fname_style:
            cmd += ["-ojf", _json_path_for(out_path, fmt)]
        else:
            cmd += ["-ojf"]
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
                 on_whisper_pct, say, json_out=False):
    """เรียก whisper.cpp ให้ได้ไฟล์ผลลัพธ์ที่ out_path แน่นอน (ไฟล์เดียว)

    เลือกสไตล์ธงจากการ probe + ลอง fallback อีกสไตล์/ตัด -pp ตามลำดับ —
    รันโดย cwd = ไดเรกทอรีของ out_path (temp) ไฟล์ข้างทางจาก build ที่ตั้งชื่อ
    ตาม input จึงตกอยู่ใน temp และถูกล้างไปด้วยเสมอ ·
    json_out: ขอ -ojf เพิ่ม (ความมั่นใจต่อ token — ผู้เรียกอ่าน <out>.json เอง)
    """
    workdir = os.path.dirname(out_path) or "."
    probe = _probe_whisper(whisper_bin)
    if probe is None:
        fname_style, has_prompt, has_json = True, True, False
        style_order = [True, False]
    else:
        fname_style, has_prompt, has_json = probe[:3]
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
            json_out=json_out and has_json,
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

    ใช้สไตล์ bool ล้วน: `-f w1 -f w2 … -otxt [-ojf]` (ไม่มี -of) → build ที่รองรับ
    multi-input (ยืนยันกับ build จริง) เขียน `<wav>.txt` (+ `<wav>.json` เมื่อมี -ojf)
    ข้าง input ทุกตัว · สไตล์ FNAME map ผลต่อไฟล์ไม่ได้ → คืน not-ok เพื่อให้ผู้เรียก
    เฟลอแบ็กเป็นรายไฟล์แทน · ไม่ใส่ -pp (progress ของ batch อิงระดับกลุ่มของเราเอง)
    คืน (ผ่านทั้งชุด?, {wav: มีไฟล์ผลแล้วหรือยัง})
    """
    probe = _probe_whisper(whisper_bin)
    if probe is None:
        fname_style, has_prompt, has_json = False, True, False
    else:
        fname_style, has_prompt, has_json = probe[:3]
    if fname_style:
        return False, {w: os.path.isfile(w + ".txt") for w in wav_paths}
    cmd = [whisper_bin, "-m", model]
    for wav in wav_paths:
        cmd += ["-f", wav]
    cmd += ["-l", lang]
    if has_json:
        cmd += ["-ojf"]
    cmd += ["-otxt"]
    if threads:
        cmd += ["-t", str(threads)]
    if prompt and has_prompt:
        cmd += ["--prompt", prompt]
    try:
        rc, _tail = _spawn_streaming_stderr(cmd, workdir, None)
    except OSError as exc:
        raise ThaisttError("เรียก whisper.cpp ไม่ได้: %s" % exc)
    done = {wav: os.path.isfile(wav + ".txt") for wav in wav_paths}
    return (rc == 0 and all(done.values())), done


def _run_whisper_detect(whisper_bin, model, wav_paths, workdir):
    """รอบตรวจภาษาแยก (ข้อ 9 ของ W1/W2): multi -f + `-l auto -dl`

    whisper จบหลัง langid (ไม่ decode — เร็วกว่าถอดเสียงเยอะ) แล้วพิมพ์ต่อไฟล์:
    `main: processing '<wav>' …` + `auto-detected language: de (p = 0.999347)`
    คืน {wav: (lang, p)} — ไฟล์ที่ไม่มีบรรทัดผล = ไม่อยู่ใน dict ·
    build ไม่มี -dl / รันพัง → คืน {} ให้ผู้เรียกเตือนและเดินต่อ (ไม่ถือว่างานพัง)
    """
    probe = _probe_whisper(whisper_bin)
    has_detect = bool(probe) and probe[3]
    if not has_detect:
        return None  # build ไม่รองรับ (ต่างจาก {} = รองรับแต่ไม่มีผล)
    cmd = [whisper_bin, "-m", model]
    for wav in wav_paths:
        cmd += ["-f", wav]
    cmd += ["-l", "auto", "-dl"]
    result = {}
    state = {"current": None}
    wav_set = set(wav_paths)

    def on_line(line):
        m = _PROCESSING_RE.match(line)
        if m:
            state["current"] = m.group(1)
            return
        m = _DETECT_RE.search(line)
        if m and state["current"] in wav_set:
            result[state["current"]] = (m.group(1), float(m.group(2)))

    try:
        _rc, _tail = _spawn_streaming_stderr(cmd, workdir, on_line)
    except OSError:
        return {}
    return result


# ---------------------------------------------------------------------------
# pipeline + CLI
# ---------------------------------------------------------------------------

def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        raise ThaisttError("อ่านไฟล์ผลลัพธ์ %s ไม่ได้: %s" % (path, exc))


def _replace_atomically(path, write):
    """เขียนไฟล์ temp ข้างปลายทางแล้ว os.replace — พังกลางการเขียนไม่ทิ้งไฟล์ขาดครึ่ง

    สำคัญกับ resume: `.txt` ที่ขาดครึ่งจะถูกนับว่า "มีผลแล้ว" และ manifest ที่ขาดครึ่ง
    ทำให้ประวัติรอบก่อนหาย
    """
    tmp_path = "%s.tmp-%d" % (path, os.getpid())
    try:
        write(tmp_path)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _write_text(path, content):
    def write(tmp_path):
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
    try:
        _replace_atomically(path, write)
    except OSError as exc:
        raise ThaisttError("เขียนไฟล์ %s ไม่ได้: %s" % (path, exc))


def _resolve_prompt(args, say):
    """--prompt ตรง ๆ หรือ --prompt-file (รายชื่อตัวละคร/ศัพท์เฉพาะ ตามข้อเสนอ W1)
    → ข้อความ prompt สำหรับ whisper (long flag --prompt) · เตือนเมื่อน่าจะโดนตัดเกินขอบ"""
    if args.prompt and args.prompt_file:
        raise ThaisttError("ใช้ --prompt หรือ --prompt-file อย่างใดอย่างหนึ่ง ไม่ใช่พร้อมกัน")
    if args.prompt_file:
        path = os.path.abspath(os.path.expanduser(args.prompt_file))
        if not os.path.isfile(path):
            raise ThaisttError("ไม่พบไฟล์ --prompt-file: %s" % path)
        joined = " ".join(_read_text(path).split())
        if not joined:
            raise ThaisttError("ไฟล์ --prompt-file ว่างเปล่า: %s" % path)
    else:
        joined = args.prompt or ""
    if joined:
        est_tokens = math.ceil(len(joined) / PROMPT_CHARS_PER_TOKEN)
        if est_tokens > PROMPT_TOKEN_LIMIT:
            say(
                "คำเตือน: คำใบ้ยาว ~%d token (ประมาณ %d ตัวอักษร) เกินขอบ ~%d token ของ "
                "whisper — ส่วนเกินถูกตัดทิ้งเงียบ ๆ (จัดลำดับชื่อสำคัญไว้ต้นไฟล์ หรือแบ่งถอดเป็นชุด)"
                % (est_tokens, len(joined), PROMPT_TOKEN_LIMIT))
    return joined


def _parse_files_from(spec):
    """--files-from FILE|'-': รายการ path บรรทัดละทาง · ข้ามบรรทัดว่าง/#comment
    (W2: เลือก id ที่ต้องการถอดโดยไม่ต้องแยกโฟลเดอร์ต่อองก์)"""
    if spec == "-":
        lines = sys.stdin.read().splitlines()
    else:
        path = os.path.abspath(os.path.expanduser(spec))
        if not os.path.isfile(path):
            raise ThaisttError("ไม่พบไฟล์ --files-from: %s" % path)
        lines = _read_text(path).splitlines()
    paths = [ln.strip() for ln in lines]
    return [p for p in paths if p and not p.startswith("#")]


def run_pipeline(args, say):
    """ห่วงหลัก 0 → 100% · คืน exit code (0/2) · ThaisttError โยนขึ้นให้ main จัด"""
    # ตรวจ --range ก่อน (ถูกสุด ผิดชี้ทันที)
    range_pair = parse_range(args.range) if args.range else None
    prompt = _resolve_prompt(args, say)

    if args.max_chars < 1:
        raise ThaisttError("--max-chars ต้อง ≥ 1 (ได้ %d)" % args.max_chars)
    if args.max_dur <= 0:
        raise ThaisttError("--max-dur ต้องเป็นบวก (ได้ %g)" % args.max_dur)
    if args.min_gap < 0:
        raise ThaisttError("--min-gap ต้อง ≥ 0 (ได้ %g)" % args.min_gap)
    if args.threads is not None and args.threads < 1:
        raise ThaisttError("-t/--threads ต้อง ≥ 1 (ได้ %d)" % args.threads)

    # โหมดไฟล์เดียว: media มีสมาชิกเดียวและไม่ใช่โฟลเดอร์ (dispatch ที่ main)
    if args.force:
        raise ThaisttError("--force ใช้กับโหมด batch เท่านั้น (โหมดไฟล์เดียวเขียนทับ output อยู่แล้ว)")
    if args.detect_lang:
        raise ThaisttError("--detect-lang ใช้กับโหมด batch เท่านั้น (ผลอยู่ใน manifest ของ batch)")
    if "," in args.lang:
        raise ThaisttError("-l หลายภาษา (คั่นด้วยลูกน้ำ) ใช้กับโหมด batch เท่านั้น "
                           "— โหมดไฟล์เดียวถอดครั้งละภาษา")
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
                args.threads, prompt, on_whisper_pct, say, json_out=True,
            )
            say("progress: 95% กำลังจัดจังหวะซับ (merge/split)…")
            segments = parse_srt(_read_text(raw_path))
            if range_pair:
                segments = shift_segments(segments, range_pair[0])
            result = retime_segments(segments, args.max_chars, args.max_dur, args.min_gap)
            _write_text(out_path, format_srt(result))
            _print_hallucination_warnings([s.text for s in result], raw_path, "srt")
            if not result:
                say("คำเตือน: ไม่พบเสียงพูดในช่วงที่ถอด — เขียน SRT เปล่า")
            say("progress: 100%% เสร็จ — %d ซับ → %s" % (len(result), out_path))
        else:
            raw_path = os.path.join(tmpdir, "raw.txt")
            _run_whisper(
                whisper_bin, model, wav_path, args.lang, raw_path, "txt",
                args.threads, prompt, on_whisper_pct, say, json_out=True,
            )
            content = _read_text(raw_path).strip()
            _write_text(out_path, content + "\n" if content else "")
            _print_hallucination_warnings([content], raw_path, "txt")
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
        "media", nargs="*", metavar="media|folder",
        help="ไฟล์สื่อที่ ffmpeg รองรับ (mp4/mkv/wav/ogg/mp3/mp2/bik/m4a/webm ฯลฯ) — "
             "หลายตัวหรือโฟลเดอร์ = โหมด batch (txt รายคลิป) · ไม่ใส่ก็ได้ถ้าใช้ --files-from")
    parser.add_argument(
        "-l", "--lang", dest="lang", default="auto", metavar="lang",
        help="ภาษาพูด เช่น en/ja/zh หรือ auto · batch ให้หลายภาษาคั่นลูกน้ำได้ "
             "เช่น -l en,pl (แยกโฟลเดอร์ผลต่อภาษา) (default: %(default)s)")
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
        "--files-from", dest="files_from", metavar="FILE",
        help="batch: รายการไฟล์ที่จะถอด บรรทัดละ path ('-' = stdin · ข้ามบรรทัดว่าง/#comment) — เลือก id เฉพาะโดยไม่ต้องแยกโฟลเดอร์")
    parser.add_argument(
        "--force", dest="force", action="store_true",
        help="batch: ถอดใหม่แม้มีผลลัพธ์อยู่แล้ว (ปกติข้ามเพื่อ resume — เช่นหลังเพิ่มชื่อใน --prompt-file)")
    parser.add_argument(
        "--detect-lang", dest="detect_lang", action="store_true",
        help="batch: ตรวจภาษาที่ได้ยินต่อคลิปอีกรอบ (รอบเดียวต่อกลุ่ม เร็ว) — ใส่ manifest "
             "เป็น lang/lang_p/lang_mismatch เพื่อจับคลิปที่ไม่ใช่ภาษาที่สั่ง (ภาษาเอลฟ์/เสียงร้อง/คาถา)")
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


# ค่าเฉลี่ยความมั่นใจต่ำกว่านี้ = น่าสงสัย (คลิปปกติของ W1 จริงอยู่ราว 0.80–0.97 —
# คำตะโกนภาษาเอลฟ์ที่บังคับถอดอังกฤษจะต่ำกว่ามาก · ปรับได้เมื่อมีข้อมูลจริงมากขึ้น)
SUSPICIOUS_AVG_P = 0.70


def _is_suspicious(duration_s, text, avg_p=None, lang_mismatch=False):
    """คลิปน่าสงสัย (ให้คนตรวจก่อน) — รวมสัญญาณทุกตัวที่มี:

    - สั้นกว่า 1 วินาที หรือ วลีหลอนที่พบบ่อย (หลักฐาน W1)
    - avg_p ต่ำกว่าเกณฑ์ (บังคับถอดผิดภาษา/เสียงร้อง ได้ token prob ต่ำ) —
      จับคำตะโกนภาษาเอลฟ์สั้น ๆ ("Aelirenn!") ที่ langid จับไม่ได้เพราะภาษาเอลฟ์
      ไม่อยู่ใน 100 ภาษาของ whisper และคำเดี่ยวสั้น ๆ มันจะเลือกภาษาใกล้เคียง
      (เช่น en p=0.97) เสมอ
    - lang_mismatch (ตรวจพบว่าไม่ใช่ภาษาที่สั่ง — ใช้ได้กับประโยคยาวพอ)
    """
    if lang_mismatch:
        return True
    if duration_s is not None and duration_s < 1.0:
        return True
    if avg_p is not None and avg_p < SUSPICIOUS_AVG_P:
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
    avg_p: object = None        # ความมั่นใจเฉลี่ยต่อ token 0..1 (จาก -ojf ของ whisper)
    low_conf_pct: object = None  # % ของ token ที่มั่นใจต่ำ (p < 0.5)
    lang: str = ""              # ภาษาที่ตรวจพบ (รอบ --detect-lang — ว่างเมื่อไม่ได้ตรวจ)
    lang_p: object = None       # ความน่าจะเป็นของภาษาที่ตรวจพบ 0..1
    resumed: bool = False       # ข้ามเพราะมีผลแล้ว — manifest คงแถวเดิมของคลิปนี้


def _lang_mismatch_value(item, requested_lang):
    """1 = ตรวจพบว่าไม่ใช่ภาษาที่สั่ง · "" = ไม่ได้ตรวจ/สั่ง auto"""
    if item.lang and requested_lang and requested_lang != "auto":
        return 0 if item.lang == requested_lang else 1
    return ""


def _batch_summary(items, requested_lang=""):
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
        if _is_suspicious(it.duration, it.text, it.avg_p,
                          _lang_mismatch_value(it, requested_lang) == 1)
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


MANIFEST_COLUMNS = ["id", "file", "status", "duration_s", "avg_p", "low_conf_pct",
                    "lang", "lang_p", "lang_mismatch", "suspicious", "text_chars",
                    "note"]


def _read_manifest_rows(path):
    """อ่าน manifest เดิม → {id: row} ตามลำดับเดิม — ไม่มีไฟล์คืน {}

    รับ manifest รุ่นเก่าที่คอลัมน์น้อยกว่าได้ (คอลัมน์ที่ขาด = ว่าง) · อ่านไม่ได้
    (เสีย/ไม่ใช่ manifest) → เก็บสำรอง `.bak-<เวลา>` + เตือน แล้วเริ่มใหม่ — ไม่ทิ้ง
    ประวัติเงียบ ๆ (หลักอนุรักษ์ข้อมูลเดิม)
    """
    if not os.path.exists(path):
        return {}
    rows = {}
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            if "id" not in (reader.fieldnames or []):
                raise ValueError("ไม่มีคอลัมน์ id")
            for row in reader:
                if row.get("id"):
                    rows[row["id"]] = [row.get(col) or "" for col in MANIFEST_COLUMNS]
    except (OSError, UnicodeDecodeError, csv.Error, ValueError) as exc:
        backup = "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
        try:
            shutil.copy2(path, backup)
            kept = "สำรองไว้ที่ %s" % backup
        except OSError as copy_exc:
            kept = "สำรองไม่ได้: %s" % copy_exc
        print("คำเตือน: อ่าน manifest เดิม %s ไม่ได้ (%s) — %s แล้วเริ่มใหม่"
              % (path, exc, kept), file=sys.stderr, flush=True)
        return {}
    return rows


def _write_manifest(items, out_dir, requested_lang=""):
    """สรุปผลรายคลิปแบบเครื่องอ่านได้ (CSV) — ฝั่งเกมไม่ต้องเดาความยาวจากขนาดไฟล์

    คอลัมน์: id · file (abs) · status (ok/empty/fail/skip) · duration_s ·
    avg_p (ความมั่นใจเฉลี่ยต่อ token 0..1 จาก -ojf — ว่างเมื่อ build ไม่รองรับ) ·
    low_conf_pct (%token ที่มั่นใจต่ำ p<0.5) · lang/lang_p/lang_mismatch (รอบ
    --detect-lang: ภาษาที่ตรวจพบ + ความน่าจะเป็น + 1 เมื่อคลิปน่าจะไม่ใช่ภาษาที่สั่ง —
    จับภาษาเอลฟ์/เสียงร้อง/คาถา ตามข้อ 9 ของ W1/W2) · suspicious · text_chars · note

    รวมกับ manifest เดิมตาม id (บั๊กที่ W1 รายงาน: แบ่งถอดหลายรอบลง outdir เดียว
    ค่าความมั่นใจรอบก่อนหาย) — คลิปที่ข้ามเพราะมีผลแล้วคงแถวเดิมไว้ · id ที่ไม่อยู่ใน
    รอบนี้คงไว้ · คลิปที่ถอดรอบนี้ (รวม --force) เขียนทับแถวของตัวเอง
    """
    path = os.path.join(out_dir, MANIFEST_NAME)
    rows = _read_manifest_rows(path)
    for it in items:
        if it.resumed and it.stem in rows:
            continue
        mismatch = _lang_mismatch_value(it, requested_lang)
        rows[it.stem] = [
            it.stem,
            it.src,
            it.status or "?",
            "" if it.duration is None else "%.2f" % it.duration,
            "" if it.avg_p is None else it.avg_p,
            "" if it.low_conf_pct is None else it.low_conf_pct,
            it.lang,
            "" if it.lang_p is None else it.lang_p,
            mismatch,
            1 if _is_suspicious(it.duration, it.text, it.avg_p,
                                 mismatch == 1) else 0,
            len(it.text),
            it.note,
        ]
    def write(tmp_path):
        with open(tmp_path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(MANIFEST_COLUMNS)
            writer.writerows(rows.values())
    try:
        _replace_atomically(path, write)
    except OSError as exc:
        raise ThaisttError("เขียนไฟล์สรุป %s ไม่ได้: %s" % (path, exc))


def run_batch(args, say):
    """batch: ขยายรายการ → resume ข้ามของเดิม → แปลงขนาน → ถอดเสียงเป็นชุด → สรุป

    รองรับหลายภาษาในคำสั่งเดียว (ข้อ 7 ของ W2): `-l en,pl` — แปลง WAV ครั้งเดียว
    แชร์ทุกภาษา · ผลแยกโฟลเดอร์ `<outdir>/<lang>/` + manifest ต่อภาษา
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

    langs = [lang.strip() for lang in args.lang.split(",") if lang.strip()]
    if len(set(langs)) != len(langs):
        raise ThaisttError("-l มีภาษาซ้ำ: %s" % args.lang)
    if len(langs) > 1 and "auto" in langs:
        raise ThaisttError(
            "-l หลายภาษาใช้ 'auto' รวมกันไม่ได้ — ระบุเป็นรายตัว เช่น -l en,pl")
    multi_lang = len(langs) > 1

    prompt = _resolve_prompt(args, say)
    media_args = list(args.media)
    if args.files_from:
        media_args.extend(_parse_files_from(args.files_from))
    sources = _expand_batch_inputs(media_args)
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

    # โฟลเดอร์ปลายทางต่อภาษา: ภาษาเดียว = outdir เดิม (คงเดิมกับ W1) ·
    # หลายภาษา = outdir/<lang>/
    def lang_dir_of(lang):
        return out_dir if not multi_lang else os.path.join(out_dir, lang)

    try:
        for lang in langs:
            os.makedirs(lang_dir_of(lang), exist_ok=True)
    except OSError as exc:
        raise ThaisttError("สร้างโฟลเดอร์ปลายทางไม่ได้: %s" % exc)

    stems = [os.path.splitext(os.path.basename(src))[0] for src in sources]

    # ชื่อ stem ชนกันเช็คครั้งเดียว (ผลเหมือนกันทุกภาษา)
    taken, collision_note = {}, {}
    for pos, stem in enumerate(stems):
        if stem in taken:
            collision_note[pos] = "ชื่อชนกับ %s — ใช้ของไฟล์แรก" % os.path.basename(taken[stem])
        else:
            taken[stem] = sources[pos]

    # รายการงานต่อภาษา (resume แยกตาม out_path ของภาษานั้น)
    items_by_lang = {}
    for lang in langs:
        lang_dir = lang_dir_of(lang)
        lst = []
        for pos, src in enumerate(sources):
            out_path = os.path.join(lang_dir, stems[pos] + ".txt")
            if pos in collision_note:
                lst.append(_BatchItem(src, stems[pos], out_path, pos,
                                      status="skip", note=collision_note[pos]))
            elif os.path.exists(out_path) and not args.force:
                lst.append(_BatchItem(src, stems[pos], out_path, pos,
                                      status="skip", resumed=True,
                                      note="มีผลลัพธ์แล้ว (resume — ใช้ --force ถอดใหม่)"))
            else:
                lst.append(_BatchItem(src, stems[pos], out_path, pos))
        items_by_lang[lang] = lst

    # กลุ่มตามตำแหน่งไฟล์ต้นทาง — แปลง WAV ครั้งเดียวแล้วใช้ร่วมทุกภาษา
    pending = [
        pos for pos in range(len(sources))
        if any(not items_by_lang[lang][pos].status for lang in langs)
    ]
    total = len(pending)
    groups = [pending[i:i + args.batch_size] for i in range(0, total, args.batch_size)]
    skipped = sum(1 for lang in langs for it in items_by_lang[lang] if it.status == "skip")
    say("progress: 5%% เริ่ม batch %d ไฟล์%s (ข้ามผลเดิม %d) — %d กลุ่ม × สูงสุด %d "
        "ไฟล์/กลุ่ม · ffmpeg ขนาน %d…"
        % (len(sources),
           " × %d ภาษา [%s]" % (len(langs), ",".join(langs)) if multi_lang else "",
           skipped, len(groups), args.batch_size, args.parallel_ffmpeg))

    state = {"last_pct": 4, "converted": 0, "processed": 0}

    def say_pct(pct, message):
        # % ต้องไปหน้าเดียว (ขึ้นเท่านั้น) — กันกระโดดถอยหลังตอนสลับเฟสระหว่างกลุ่ม
        pct = min(95, max(5, pct))
        if pct > state["last_pct"]:
            state["last_pct"] = pct
            say("progress: %d%% %s" % (pct, message))

    # ทำทีละกลุ่ม: temp ถูกจำกัดขนาดต่อกลุ่มแทนการแปลงทั้งหมดก่อน —
    # สำคัญกับชุดใหญ่ (W1 ~18,000 คลิป จะไม่มี WAV กองหลาย GB ใน temp)
    for seq, positions in enumerate(groups, 1):
        with tempfile.TemporaryDirectory(prefix="thaistt-batch-") as tmpdir:
            wavs_dir = os.path.join(tmpdir, "wavs")
            os.makedirs(wavs_dir, exist_ok=True)

            lock = threading.Lock()
            wav_by_pos = {}
            conv_fail = {}

            def convert(pos):
                wav_path = os.path.join(wavs_dir, "%06d__%s.wav" % (pos, stems[pos]))
                ok, err = _run_ffmpeg_one(ffmpeg_bin, sources[pos], wav_path)
                with lock:
                    if ok:
                        wav_by_pos[pos] = (wav_path, _read_wav_duration_seconds(wav_path))
                    else:
                        conv_fail[pos] = err
                    state["converted"] += 1
                    say_pct(5 + 55 * state["converted"] // max(1, total),
                            "แปลงเสียง %d/%d… (กลุ่ม %d/%d)"
                            % (state["converted"], total, seq, len(groups)))

            if positions:
                with concurrent.futures.ThreadPoolExecutor(
                        max_workers=min(args.parallel_ffmpeg, len(positions))) as pool:
                    list(pool.map(convert, positions))

            # กระจายผลแปลง (แชร์ทุกภาษา) — พังครั้งเดียว = พังทุกภาษา
            for lang in langs:
                for pos in positions:
                    it = items_by_lang[lang][pos]
                    if it.status:
                        continue
                    if pos in conv_fail:
                        it.status, it.note = "fail", "ffmpeg %s" % conv_fail[pos]
                    else:
                        it.wav, it.duration = wav_by_pos[pos]

            # ถอดเสียงต่อภาษา (รอบเดียวต่อกลุ่ม×ภาษา)
            for lang in langs:
                ready = [items_by_lang[lang][pos] for pos in positions
                         if not items_by_lang[lang][pos].status]
                if not ready:
                    continue
                wavs = [it.wav for it in ready]
                _ok, done = _run_whisper_multi(
                    whisper_bin, model, wavs, lang, args.threads, prompt,
                    wavs_dir)
                for it in ready:  # คลิปที่ชุดใหญ่ไม่ได้ผล → เรียกรายไฟล์แยกคลิปพิษ
                    if not done.get(it.wav):
                        try:
                            _run_whisper(whisper_bin, model, it.wav, lang,
                                         it.wav + ".txt", "txt", args.threads,
                                         prompt, None, say, json_out=True)
                        except ThaisttError as exc:
                            it.status, it.note = "fail", str(exc)
                for it in ready:
                    if it.status == "fail":
                        continue
                    raw = it.wav + ".txt"
                    if os.path.isfile(raw):
                        it.text = _read_text(raw).strip()
                        it.status = "ok" if it.text else "empty"
                        conf = _confidence_from_json(it.wav + ".json")
                        if conf:
                            it.avg_p, it.low_conf_pct = conf
                    else:
                        it.status, it.note = "fail", "whisper ไม่ได้ผลลัพธ์"

            # รอบตรวจภาษา (ข้อ 9 ของ W1/W2): ทำครั้งเดียวต่อกลุ่ม แชร์ทุกภาษา —
            # จับคลิปภาษาเอลฟ์/เสียงร้อง/คาถา ที่ไม่ใช่ภาษาที่สั่ง ใส่ manifest เป็น lang/lang_p
            if args.detect_lang:
                detectable = sorted({
                    it.wav for lang in langs for it in items_by_lang[lang]
                    if it.status in ("ok", "empty") and it.wav
                })
                if detectable:
                    detected = _run_whisper_detect(
                        whisper_bin, model, detectable, wavs_dir)
                    if detected is None and not state.get("warned_no_detect"):
                        state["warned_no_detect"] = True
                        say("คำเตือน: build ของ whisper.cpp นี้ไม่มี -dl "
                            "(--detect-language) — ข้ามการตรวจภาษา")
                    for lang in langs:
                        for it in items_by_lang[lang]:
                            if it.wav in (detected or {}):
                                it.lang, it.lang_p = detected[it.wav]
            # เขียนผลท้ายทุกกลุ่ม (ไม่รอจบทั้งงาน) — รันยาวพังกลางทาง (Ctrl-C/ไฟดับ)
            # เสียแค่กลุ่มที่กำลังทำ รันซ้ำ resume ต่อได้จริง · manifest อัปเดตด้วย
            # จึงคงค่าความมั่นใจของกลุ่มที่เสร็จแล้ว
            for lang in langs:
                lang_items = items_by_lang[lang]
                for pos in positions:
                    it = lang_items[pos]
                    if it.status in ("ok", "empty"):
                        _write_text(it.out_path, it.text + "\n" if it.text else "")
                _write_manifest([it for it in lang_items if it.status],
                                lang_dir_of(lang), requested_lang=lang)
            state["processed"] += len(positions)
            say_pct(60 + 35 * state["processed"] // max(1, total),
                    "ถอดเสียงกลุ่ม %d/%d เสร็จ%s…"
                    % (seq, len(groups),
                       " (พร้อมตรวจภาษา)" if args.detect_lang else ""))

    # ผล .txt เขียนไปแล้วท้ายแต่ละกลุ่ม (ok/empty เขียนเสมอ ว่าง = ไฟล์เปล่า) —
    # ตรงนี้เขียน manifest ปิดท้ายครบทุกคลิป (รวมรอบที่ข้ามหมดจนไม่มีกลุ่มเลย)
    all_items = []
    for lang in langs:
        lang_items = items_by_lang[lang]
        _write_manifest(lang_items, lang_dir_of(lang), requested_lang=lang)
        all_items.extend(lang_items)

    say("progress: 100%% เสร็จ — ผลอยู่ที่ %s (สรุปรายคลิป: %s)" % (out_dir, MANIFEST_NAME))
    fail_count = _batch_summary(all_items, requested_lang=langs[0])
    return 2 if fail_count else 0


def _hallucination_warnings(sub_texts, avg_p):
    """คำเตือนว่า "ไฟล์นี้น่าจะไม่มีบทพูดจริง" สำหรับโหมดไฟล์เดียว (Inbox W1 3 พ.ย.)

    sub_texts: ข้อความรายซับของไฟล์ (โหมด txt ใส่ทั้งข้อความเป็นสมาชิกเดียว) ·
    avg_p: ความมั่นใจเฉลี่ยต่อ token จาก -ojf (None เมื่อ build ไม่รองรับ)
    คืน list ข้อความเตือน (ว่าง = ดูปกติ) — **ไม่แตะข้อมูลใน output แต่อย่างใด**
    (ผู้ใช้เลือกแนวทางเตือนอย่างเดียว 5 พ.ย.)
    """
    warnings = []
    if avg_p is not None and avg_p < SUSPICIOUS_AVG_P:
        warnings.append(
            "ความมั่นใจเฉลี่ยต่ำ (avg_p=%.2f < %.2f) — ไฟล์นี้อาจไม่มีเสียงพูดจริง "
            "หรือถอดได้ไม่น่าเชื่อถือ แนะนำตรวจก่อนนำไปใช้" % (avg_p, SUSPICIOUS_AVG_P))
    hits = [t.strip() for t in sub_texts if t.strip() and HALLUCINATION_SIGN_RE.search(t)]
    if hits:
        sample = " / ".join('"%s"' % h[:40] for h in hits[:3])
        warnings.append(
            "พบลายเซ็นข้อความหลอนของ Whisper (%s) — มักเกิดบนช่วงดนตรี/เสียงประกอบล้วน" % sample)
    texts = [" ".join(t.lower().split()) for t in sub_texts if t.strip()]
    if len(texts) >= 6:
        unique = len(set(texts))
        if 1 - unique / len(texts) >= DUPLICATE_RATIO_THRESHOLD:
            warnings.append(
                "ซับซ้ำกันมากผิดปกติ (%d จาก %d ซับเป็นข้อความเดิม) — ลักษณะข้อความหลอน"
                "บนเสียงไม่มีบทพูด" % (len(texts) - unique, len(texts)))
    return warnings


def _print_hallucination_warnings(sub_texts, raw_path, fmt):
    """คำนวณ + พิมพ์คำเตือนที่ stderr เสมอ (แม้ --quiet — สัญญาณระดับผลลัพธ์ ไม่ใช่ progress)"""
    conf = _confidence_from_json(_json_path_for(raw_path, fmt))
    avg_p = conf[0] if conf else None
    for message in _hallucination_warnings(sub_texts, avg_p):
        print("%s: คำเตือน: %s" % (APP, message), file=sys.stderr, flush=True)


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
    if not args.media and not args.files_from:
        parser.error("ต้องระบุ media (ไฟล์/โฟลเดอร์) อย่างน้อยหนึ่งตัว หรือใช้ --files-from")
    if args.quiet:
        say = lambda _msg: None
    else:
        say = lambda msg: print(msg, file=sys.stderr, flush=True)
    batch_mode = (
        len(args.media) > 1
        or bool(args.files_from)
        or any(os.path.isdir(os.path.expanduser(p)) for p in args.media)
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
