# AGENT.md — คู่มือสำหรับ AI agent ที่ทำงานใน thaistt

ถอดเสียง/วิดีโอเป็นซับฉบับร่าง (SRT) ด้วย whisper.cpp + ffmpeg แบบ subprocess —
เพื่อส่งต่อเข้า pipeline แปลไทยของ TStudio (workspace "THub Studio")

- ตอบ/เขียนเอกสารเป็นภาษาไทย · สถานะงานล่าสุดอยู่ที่ `.handover/HANDOVER.md`
  (commit เข้า repo ตามแบบแผน ecosystem) · สเปกฉบับเต็ม:
  `../THub Studio/docs/spec-vox-whisper.md` → "Phase S0"
- หลักการใหญ่: **อนุรักษ์ข้อมูลเดิมเสมอ** — ไม่เขียนทับไฟล์ต้นฉบับ · เขียนเฉพาะไฟล์ output
  ที่ระบุ · temp ทำความสะอาดเองแม้รันพังกลางทาง

## สภาพแวดล้อม / คำสั่งหลัก

```bash
python3 -m unittest discover -s tests -v        # ทดสอบ — ต้องผ่านก่อนจบทุกครั้ง
python3 thaistt.py --help                       # CLI (เมื่อ implement แล้ว)
```

- ตัวถ่วงน้ำหนักจริง (whisper.cpp, ffmpeg) เป็น **external binary** — หาผ่าน args หรือ env
  `THAISTT_WHISPER` / `THAISTT_MODEL` / `THAISTT_FFMPEG` หรือ PATH

## โครงสร้าง

| ที่อยู่ | หน้าที่ |
|---|---|
| `thaistt.py` | CLI เดียวจบ: ไฟล์เดียว (v0.1) ffmpeg → whisper.cpp → ชั้นจัดจังหวะซับ → SRT/txt · batch รายคลิป (v0.2) multi `-f` รอบเดียวต่อชุด → txt + สรุป |
| `tests/` | unit (ชั้นจัดจังหวะใช้ SRT ดิบจำลองได้ ไม่ต้องมี whisper) + fixture สื่อจริง |
| `NOTICE` | เครดิตส่วนประกอบบุคคลที่สาม — ต้องรักษาไว้ทุกครั้งที่แก้ |

## กฎเหล็ก

1. **Python stdlib-only** — ไม่เพิ่ม dependency ภายนอก (งานหนักอยู่ที่ external binary อยู่แล้ว)
2. **ห้าม commit โมเดล/ไบนารี/ไฟล์สื่อตัวอย่างขนาดใหญ่** — fixture เสียงสั้น ๆ หรือสร้างแบบ
   synthetic ด้วยสคริปต์แทน
3. **CLI contract ตามสเปก workspace** — อาร์กิวเมนต์/exit codes (0 สำเร็จ · 2 พัง) ห้ามเปลี่ยน
   โดยไม่ประสานกับ `spec-vox-whisper.md` (ผู้บริโภคคือ TVox ใน THub-Launcher)
4. เทสใหม่ประกอบทุกฟีเจอร์/ทุก bugfix · CI (GitHub Actions) ต้องเขียวทุก push
5. จบ session ทำงาน → อัปเดต `.handover/HANDOVER.md` พร้อม commit hash เสมอ
