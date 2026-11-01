# HANDOVER — thaistt

**สร้าง:** 2026-11-01 โดย **workspace task (hub)** ของ "THub Studio" ตามคำสั่งเจ้าของ
(ตัดสินใจแยก STT เป็น repo เฉพาะ · ชื่อ/ตำแหน่งเลือกโดยเจ้าของ: `thaistt` ที่ Playgound)

## สถานะปัจจุบัน

- **v0.1 เสร็จ (Phase S0 ครบ acceptance)** — commit `06b3715` (2026-11-01)
  โดย thaistt session ตามสเปก `../THub Studio/docs/spec-vox-whisper.md` → "Phase S0"
- CLI ครบ contract: `thaistt <media> -l <lang|auto> -o <draft.srt> [--whisper/--model/
  --ffmpeg/--format srt|txt/--max-chars 42/--max-dur 7/--min-gap 0.8/--range/--prompt/
  -t/--quiet]` · exit 0/2 · stderr ไทย + `progress: NN%` · ไม่แตะต้นฉบับ · temp
  ล้างเองแม้พังกลางทาง (ทดสอบแล้วทั้งเคส ffmpeg/whisper พัง)
- ชั้นจัดจังหวะซับ: merge ต่อเมื่อ gap < min-gap และ ≤ max-chars/max-dur · ท่อนเดี่ยว
  ยาวเกินถูกซอยที่ช่องว่างคำ (เวลาแบ่งตามสัดส่วนอักขระ) · เลขลำดับเรียงใหม่
- **ข้อค้นพบสำคัญตอน implement** (จำเป็นต่อการดูแล): ธง output ของ whisper.cpp มี
  สองสไตล์ — build ใหม่บางรุ่น `-osrt <FNAME>` แต่ build ปัจจุบัน (homebrew 2026)
  เป็น `-of <prefix>` + `-osrt` (bool) และ `-p` กลายเป็น processors → thaistt probe
  จาก `--help` + fallback อีกสไตล์ + ใช้ long flag `--prompt` เท่านั้น + รัน whisper
  ด้วย cwd=temp (build เก่าเขียนไฟล์ข้างทางที่ CWD เมื่อสไตล์ผิด)
- เทส 52 ตัว: unit ชั้นจัดจังหวะ (SRT ดิบจำลอง) + CLI error paths + integration
  ด้วย fake binaries (`tests/fakes/` — CI ไม่ต้องมี whisper) + fixture จริง
  (`tests/test_fixture_real.py` skip explicit ตามสเปกเมื่อเครื่องไม่ครบเครื่องมือ
  — รันจริงด้วย `THAISTT_TEST_MODEL=<ggml>` → jfk.wav + large-v3-turbo ผ่าน)
- **ยังไม่มี GitHub remote** — CI (ubuntu+windows · py3.9/3.12) ยังไม่เคยรันจริง
  (คำสั่งเดียวกับ CI ผ่านครบในเครื่องบน python 3.9.6 แล้ว) · รอเจ้าของสั่งสร้าง
  (AeyXD/thaistt) → push แล้วดู Actions เขียว

## งานถัดไป

- **v0.2 batch รายคลิป** (จาก use case ThaiW1Setup/ThaiW2Setup): รับโฟลเดอร์/หลายไฟล์
  `--format txt` → แปลง ffmpeg ขนาน → เรียก whisper-cli รอบเดียวต่อชุด (default 250)
  reuse การโหลดโมเดล ~8s · ทนคลิปพังบางส่วน — ตามสเปกหัวข้อ "v0.2"
- ผู้บริโภค: **TVox ใน THub-Launcher** (Phase S2 — Settings `stt_cli_path` + ปุ่มถอดเสียง
  + dialog parse `progress: NN%`) · ผ่านสะพาน: SRT ร่าง → Game Tool "Subtitles" ของ
  TFormat-Plugins (Phase S1) → CSV กลาง → pipeline แปลไทยเต็มสาย

## บทบาทในภาพรวม

ผู้บริโภคหลัก: **TVox ใน THub-Launcher** (Phase S2 — Settings `stt_cli_path` + ปุ่มถอดเสียง) ·
ผ่านสะพาน: SRT ร่าง → Game Tool "Subtitles" ของ TFormat-Plugins (Phase S1) → CSV กลาง →
pipeline แปลไทยเต็มสาย
