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
  printf '  -ojf,      --output-json-full     [false  ] include more information in the JSON file\n'
  if [ -z "$THAISTT_TEST_WHISPER_NODL" ]; then
    printf '  -dl,       --detect-language      [false  ] exit after automatically detecting language\n'
  fi
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
want_json=0
want_dl=0
for a in "$@"; do
  [ "$a" = "-ojf" ] && want_json=1
  [ "$a" = "-dl" ] && want_dl=1
done
if [ "$want_dl" = "1" ]; then
  # เลียน -dl ของ build จริง: จบหลังตรวจภาษา (ไม่เขียนไฟล์ผล) — ต่อไฟล์:
  # main: processing '<f>' … + whisper_full_with_state: auto-detected language: X (p = 0.99)
  lang="${THAISTT_TEST_WHISPER_LANG:-en}"
  echo "$inputs" | while IFS= read -r f; do
    [ -n "$f" ] || continue
    printf "main: processing '%s' (16000 samples, 1.0 sec), 4 threads, lang = auto ...\n" "$f" >&2
    printf "whisper_full_with_state: auto-detected language: %s (p = 0.990000)\n" "$lang" >&2
  done
  exit 0
fi
emit_json() {
  # เลียน -ojf ของ build จริง: token ละ p — ข้อความ 4 token (0.2/0.9/0.95/0.61)
  # → avg_p 0.665 · low_conf_pct 25 · [_BEG_] เป็น token พิเศษไม่นับ
  [ "$want_json" = "1" ] || return 0
  printf '%s' '{"transcription":[{"text":"x","tokens":[{"text":"[_BEG_]","p":0.2},{"text":" Hey","p":0.2},{"text":" there","p":0.9},{"text":" friend.","p":0.95},{"text":" now","p":0.61}]}]}' > "$1"
}
if [ -z "$target" ]; then
  # batch: ผลลัพธ์ข้างไฟล์ input ทุกตัว
  echo "$inputs" | while IFS= read -r f; do
    [ -n "$f" ] || continue
    printf '%s\n' "$text" > "$f.txt"
    emit_json "$f.json"
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
if [ "$want_json" = "1" ]; then
  echo "$inputs" | while IFS= read -r f; do
    [ -n "$f" ] && emit_json "$f.json"
  done
fi
