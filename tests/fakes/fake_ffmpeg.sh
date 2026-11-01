#!/bin/sh
# ffmpeg ปลอมสำหรับเทส integration ของ thaistt (POSIX เท่านั้น)
# - บันทึก argv บรรทัดละ 1 อาร์กิวเมนต์ ลง $THAISTT_TEST_ARGLOG_FF
# - สร้างไฟล์ WAV ปลอมที่ argv ตัวสุดท้าย (คือ output ของ thaistt)
# - $THAISTT_TEST_FFMPEG_FAIL=1 -> จำลอง ffmpeg พัง (exit 1)
[ -n "$THAISTT_TEST_ARGLOG_FF" ] || exit 9
printf '%s\n' "$@" >> "$THAISTT_TEST_ARGLOG_FF"
if [ -n "$THAISTT_TEST_FFMPEG_FAIL" ]; then
  echo "fake ffmpeg: boom" >&2
  exit 1
fi
last=""
for a in "$@"; do last="$a"; done
[ -n "$last" ] || exit 1
printf 'RIFF-fake' > "$last" || exit 1
