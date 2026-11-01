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
- **ขึ้น GitHub แล้ว: https://github.com/AeyXD/thaistt (public ตามธรรมเนียม ecosystem)**
  — CI **เขียวครบ matrix** (ubuntu/windows × py3.9/3.12) หลัง push fix `68736d0`
- บั๊กแรกที่ CI จับได้และแก้แล้ว (commit `68736d0`): Windows console ที่ codepage
  เดิม (cp1252) ทำให้ `--help`/ข้อความไทยพังด้วย UnicodeEncodeError → เพิ่ม
  `_force_utf8_streams()` บังคับ stdout/stderr UTF-8 ที่ต้นทาง (ไม่ใช่แก้ CI ปะกัน —
  ผู้ใช้ Windows จริงก็เจอเหมือนกัน) + regression test รัน `--help` จริงด้วย
  `PYTHONIOENCODING=cp1252` (แดงก่อนแก้ เขียวหลังแก้ ตรวจด้วย git stash แล้ว)

## งานถัดไป

- **v0.2 batch รายคลิป** (จาก use case ThaiW1Setup/ThaiW2Setup): รับโฟลเดอร์/หลายไฟล์
  `--format txt` → แปลง ffmpeg ขนาน → เรียก whisper-cli รอบเดียวต่อชุด (default 250)
  reuse การโหลดโมเดล ~8s · ทนคลิปพังบางส่วน — ตามสเปกหัวข้อ "v0.2"
  **โดยใช้ "ข้อเสนอแนะจาก ThaiW1Setup" ด้านล่างเป็นความต้องการจริง** (v0.2 ต้องดีพอ
  ทดแทนสคริปต์ชั่วคราวของ W1 ได้จริง — batch `-f` หลายไฟล์ต่อคำสั่งเดียว · ผลออก
  `<ไฟล์>.wav.txt` · ติดป้ายคลิปน่าสงสัย · `--prompt-file` ชื่อเฉพาะ)
- ผู้บริโภค: **TVox ใน THub-Launcher** (Phase S2 — Settings `stt_cli_path` + ปุ่มถอดเสียง
  + dialog parse `progress: NN%`) · ผ่านสะพาน: SRT ร่าง → Game Tool "Subtitles" ของ
  TFormat-Plugins (Phase S1) → CSV กลาง → pipeline แปลไทยเต็มสาย

## ข้อเสนอแนะจาก ThaiW1Setup → ความต้องการจริงของ v0.2 (2026-11-01 — ผู้ใช้สั่งให้บันทึก)

**เป้าหมายของส่วนนี้: ให้ thaistt v0.2 ทดแทนสคริปต์ชั่วคราวของ ThaiW1Setup**
(xargs + run.sh + compare.py ที่เรียก whisper.cpp ตรง ๆ) — คือความต้องการจากงานจริง
ที่นำมากำหนด scope ของ v0.2 ไม่ใช่บันทึกไว้อ่านอ้างอิง

ThaiW1Setup ใช้ whisper.cpp (ไม่ผ่าน thaistt) ตรวจซับเทียบเสียงพากย์อังกฤษ W1 — สคริปต์ชั่วคราว + วิธีใช้อยู่ที่
`../ThaiW1Setup/docs/whisper.md` และ `../ThaiW1Setup/build/translate/whisper/compare.py`

1. **batch รายคลิป = จำเป็นจริง (ยืนยันตัวเลขให้ v0.2)** — W1 มีเสียงพากย์ 20,839 คลิป (16.9 ชม.) / โหลดโมเดล ~8 วินาที
   ต่อครั้ง → เรียกทีละไฟล์ทั้งเกม ≈ 46 ชม. เสียเปล่า / ส่ง `-f a.wav -f b.wav …` ชุดละ 250 ไฟล์ในคำสั่งเดียว:
   2,474 คลิป (2.1 ชม.) ใช้ 19 นาทีบน Apple M5 (large-v3-turbo, `-t 8`) — ผลลัพธ์ `-otxt` ออก `<ไฟล์>.wav.txt` ข้างไฟล์
2. **คลิปสั้น/เสียงร้องได้ข้อความหลอน** — เช่น "Thank you." "*Gunshot*" บนเสียงสั้นหรือเสียงคำราม (W1: 18/621 บรรทัด
   ที่ตรวจเป็นกรณี Whisper ถอดผิด) → เสนอ: เปิด VAD ของ whisper.cpp (`--vad` + โมเดล silero) หรือเกณฑ์ no-speech
   และ**ติดป้ายคลิปที่น่าสงสัย**ในผลลัพธ์ (เช่น คลิป < 1 วินาที / ข้อความเป็นวลีหลอนที่พบบ่อย) ให้คนตรวจรู้
3. **`--prompt` ชื่อเฉพาะได้ผลจริง** — W1 ถอด "Eskel" เป็น "Pascal", "Barghest" เป็น "bar guest", ภาษาเอลฟ์เพี้ยนหมด →
   เสนอรับไฟล์รายชื่อ (เช่น `--prompt-file` จากตารางชื่อของโปรเจกต์เกม) แทนพิมพ์ยาวในบรรทัดคำสั่ง
4. **โมเดลที่ติดตั้งในเครื่องแล้ว**: `~/.local/share/whisper-models/ggml-large-v3-turbo.bin` (1.6 GB, sha1
   `4af2b29d7ec73d781377bfd1758ca957a807e941`) — ตั้ง `THAISTT_MODEL` ชี้ที่นี่ / อาจให้ thaistt ค้นตำแหน่งนี้เป็นค่าปริยาย
5. **ถอดเสียงภาษาไทย**: turbo แม่นไทยน้อยกว่า large-v3 เต็ม (~3 GB) / มีโมเดลที่ฝึกเพิ่มสำหรับไทย (เช่น Thonburian
   Whisper) แต่ต้องแปลงเป็น ggml ก่อนใช้กับ whisper.cpp — ถ้าจะรองรับ ควรระบุในเอกสารวิธีแปลง/ทดสอบ
6. **ThaiW1Setup = กรณีทดสอบขนาดใหญ่** — เมื่อ v0.2 batch เสร็จ งานตรวจเสียงพากย์ W1 ที่เหลือ (องก์ 2 ถึงบทส่งท้าย
   ~18,000 คลิป) ย้ายมาเรียก thaistt แทนสคริปต์ชั่วคราวได้ (ไฟล์เสียงแตกไว้ที่ `../ThaiW1Setup/work/voices/*.ogg`)
   — ผลที่พบจากนำร่อง: เสียงพากย์ EE ต่างจากข้อความในไฟล์เกม 52% → การเทียบควรให้คะแนนความคล้ายระดับคำ
   แล้วให้คน/AI ตัดสินความหมาย ไม่ฟ้องจากคะแนนอย่างเดียว

## บทบาทในภาพรวม

ผู้บริโภคหลัก: **TVox ใน THub-Launcher** (Phase S2 — Settings `stt_cli_path` + ปุ่มถอดเสียง) ·
ผ่านสะพาน: SRT ร่าง → Game Tool "Subtitles" ของ TFormat-Plugins (Phase S1) → CSV กลาง →
pipeline แปลไทยเต็มสาย
