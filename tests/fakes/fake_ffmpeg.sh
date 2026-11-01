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
# header: RIFF size = 36+data · fmt PCM/1ch/16000Hz/16bit
if [ -n "$THAISTT_TEST_FFMPEG_SHORT" ]; then
  printf 'RIFF\xa4\x3e\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80\x3e\x00\x00\x80\x7d\x00\x00\x02\x00\x10\x00data\x80\x3e\x00\x00' > "$last"
  dd if=/dev/zero bs=16000 count=1 >> "$last" 2>/dev/null
else
  printf 'RIFF\x24\x7d\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80\x3e\x00\x00\x80\x7d\x00\x00\x02\x00\x10\x00data\x80\x7d\x00\x00' > "$last"
  dd if=/dev/zero bs=32000 count=1 >> "$last" 2>/dev/null
fi
