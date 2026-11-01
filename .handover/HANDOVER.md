# HANDOVER — thaistt

**สร้าง:** 2026-11-01 โดย **workspace task (hub)** ของ "THub Studio" ตามคำสั่งเจ้าของ
(ตัดสินใจแยก STT เป็น repo เฉพาะ · ชื่อ/ตำแหน่งเลือกโดยเจ้าของ: `thaistt` ที่ Playgound)

## สถานะปัจจุบัน

- **v0 skeleton** — เอกสารประตูครบ (README · AGENT.md · LICENSE MIT · NOTICE · CI ·
  .gitignore กันโมเดล/สื่อ · `thaistt.py` stub · smoke test) · commit แรกโดย workspace task
- **ยังไม่มี GitHub remote** — รอเจ้าของสั่งสร้าง (AeyXD/thaistt)
- **งานถัดไป = Phase S0 (v0.1)** ตามสเปก `../THub Studio/docs/spec-vox-whisper.md`:
  1. implement CLI ตาม contract ในสเปก (ffmpeg → whisper.cpp → ชั้นจัดจังหวะซับ → SRT)
  2. ชั้นจัดจังหวะ (merge ≤ max-chars/max-dur · split ตรง min-gap) + unit ด้วย SRT ดิบจำลอง
  3. exit codes 0/2 + stderr ไทย + progress % + temp cleanup แม้พังกลางทาง
  4. เทสครบ + CI เขียว → commit ใน repo นี้ (ตามธรรมเนียม: จบ session อัปเดตไฟล์นี้)

## บทบาทในภาพรวม

ผู้บริโภคหลัก: **TVox ใน THub-Launcher** (Phase S2 — Settings `stt_cli_path` + ปุ่มถอดเสียง) ·
ผ่านสะพาน: SRT ร่าง → Game Tool "Subtitles" ของ TFormat-Plugins (Phase S1) → CSV กลาง →
pipeline แปลไทยเต็มสาย
