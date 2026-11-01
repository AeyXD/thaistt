#!/bin/sh
# ffmpeg ปลอมสำหรับเทส integration ของ thaistt (POSIX เท่านั้น)
# - บันทึก argv บรรทัดละ 1 อาร์กิวเมนต์ ลง $THAISTT_TEST_ARGLOG_FF
# - เขียนไฟล์ WAV จริง 44-byte header (16000Hz mono 16-bit) + เงียบ ณ argv ตัวสุดท้าย
#   ให้ชั้นอ่าน duration ของ thaistt (wave module) ทำงานจริง
#   $THAISTT_TEST_FFMPEG_SHORT=1 -> ความยาว 0.5 วินาที (default 1.0)
# - $THAISTT_TEST_FFMPEG_FAIL=1 -> พังทุกครั้ง (exit 1)
# - $THAISTT_TEST_FFMPEG_FAILSUBSTR=xxx -> พังเฉพาะครั้งที่ argv มี xxx (จำลองคลิปเสีย)
[ -n "$THAISTT_TEST_ARGLOG_FF" ] || exit 9
printf '%s\n' "$@" >> "$THAISTT_TEST_ARGLOG_FF"
for a in "$@"; do
  case "$a" in *"$THAISTT_TEST_FFMPEG_FAILSUBSTR"*)
    [ -n "$THAISTT_TEST_FFMPEG_FAILSUBSTR" ] && { echo "fake ffmpeg: bad clip" >&2; exit 1; }
  ;; esac
done
if [ -n "$THAISTT_TEST_FFMPEG_FAIL" ]; then
  echo "fake ffmpeg: boom" >&2
  exit 1
fi
last=""
for a in "$@"; do last="$a"; done
[ -n "$last" ] || exit 1
# เขียน WAV จริงด้วย wave module ของ python3 (พกพาข้าม sh ทุกตัว — อย่าใช้
# printf \xHH เพราะ dash บน ubuntu ไม่รองรับ) · 16000Hz mono 16-bit ·
# $THAISTT_TEST_FFMPEG_SHORT=1 → 0.5 วินาที (default 1.0)
python3 - "$last" <<'PYEOF'
import os
import sys
import wave

with wave.open(sys.argv[1], "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    seconds = 0.5 if os.environ.get("THAISTT_TEST_FFMPEG_SHORT") else 1.0
    w.writeframes(b"\x00\x00" * int(16000 * seconds))
PYEOF
