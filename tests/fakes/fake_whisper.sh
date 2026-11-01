#!/bin/sh
# whisper-cli ปลอมสำหรับเทส integration ของ thaistt (POSIX เท่านั้น)
# - ตอบ --help แบบ build "-of + bool" (ไม่มี FNAME) และมี --prompt
#   -> probe ของ thaistt เลือกสไตล์ bool (เหมือน whisper.cpp homebrew จริง)
# - บันทึก argv บรรทัดละ 1 อาร์กิวเมนต์ ลง $THAISTT_TEST_ARGLOG_WH
# - มี "explicit target" (-osrt/-otxt/-of ตามด้วยค่าไม่ขึ้นต้น '-') = โหมดไฟล์เดียว:
#   เขียน canned SRT/txt ที่ target (รับทั้งสไตล์ FNAME และ -of prefix)
# - ไม่มี explicit target = โหมด batch (multi -f + -otxt ล้วน):
#   เขียน "<input>.txt" ข้างทุกไฟล์ (เลียนแบบ whisper.cpp build จริง)
#   เนื้อหาจาก $THAISTT_TEST_WHISPER_TEXT (default "Hello there friend.")
# - $THAISTT_TEST_WHISPER_FAIL=1 -> พังทุกครั้ง (exit 1)
[ -n "$THAISTT_TEST_ARGLOG_WH" ] || exit 9
printf '%s\n' "$@" >> "$THAISTT_TEST_ARGLOG_WH"
if [ -n "$THAISTT_TEST_WHISPER_FAIL" ]; then
  echo "fake whisper: boom" >&2
  exit 1
fi
if [ "$1" = "--help" ]; then
  printf '  -t N,      --threads N            [4      ] number of threads\n'
  printf '  -osrt,     --output-srt           [false  ] output result in a srt file\n'
  printf '  -otxt,     --output-txt           [false  ] output result in a text file\n'
  printf '  -of FNAME, --output-file FNAME    [       ] output file path (without file extension)\n'
  printf '             --prompt PROMPT        [       ] initial prompt\n'
  exit 0
fi
mode=""
target=""
prev=""
inputs=""
for a in "$@"; do
  case "$a" in -osrt|-otxt) mode="$a" ;; esac
  case "$prev" in
    -osrt|-otxt|-of)
      case "$a" in -*) ;; *) target="$a" ;; esac
      ;;
  esac
  case "$prev" in
    -f)
      case "$a" in -*) ;; *) inputs="$inputs
$a" ;; esac
      ;;
  esac
  prev="$a"
done
text="${THAISTT_TEST_WHISPER_TEXT:-Hello there friend.}"
if [ -z "$target" ]; then
  # batch: ผลลัพธ์ข้างไฟล์ input ทุกตัว
  echo "$inputs" | while IFS= read -r f; do
    [ -n "$f" ] || continue
    printf '%s\n' "$text" > "$f.txt"
  done
  exit 0
fi
case "$target" in
  *.srt|*.txt) ;;
  *) if [ "$mode" = "-otxt" ]; then target="$target.txt"; else target="$target.srt"; fi ;;
esac
if [ "$mode" = "-otxt" ]; then
  printf '%s\n' "$text" > "$target"
else
  cat > "$target" <<'SRT'
1
00:00:00,000 --> 00:00:01,500
Hi.

2
00:00:01,700 --> 00:00:03,200
Hello there friend.

3
00:00:05,000 --> 00:00:07,500
This part comes after a long pause.
SRT
fi
