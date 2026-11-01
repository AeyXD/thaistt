#!/bin/sh
# whisper-cli ปลอมสำหรับเทส integration ของ thaistt (POSIX เท่านั้น)
# - ตอบ --help แบบ build "-of + bool" (ไม่มี FNAME) และมี --prompt
#   -> ทำให้ probe ของ thaistt เลือกสไตล์ bool (เหมือน whisper.cpp homebrew จริง)
# - บันทึก argv บรรทัดละ 1 อาร์กิวเมนต์ ลง $THAISTT_TEST_ARGLOG_WH
# - เขียนผลลัพธ์ SRT/txt ตามธง -osrt/-otxt (รับทั้งสไตล์ FNAME และ -of prefix)
# - $THAISTT_TEST_WHISPER_FAIL=1 -> จำลอง whisper พังทุกครั้ง (exit 1)
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
out=""
prev=""
for a in "$@"; do
  case "$a" in -osrt|-otxt) mode="$a" ;; esac
  case "$prev" in -osrt|-otxt|-of) out="$a" ;; esac
  prev="$a"
done
if [ -z "$out" ]; then
  echo "fake whisper: no output arg" >&2
  exit 1
fi
case "$out" in
  *.srt|*.txt) ;;
  *) if [ "$mode" = "-otxt" ]; then out="$out.txt"; else out="$out.srt"; fi ;;
esac
if [ "$mode" = "-otxt" ]; then
  printf 'Hello there friend.\n' > "$out"
else
  cat > "$out" <<'SRT'
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
