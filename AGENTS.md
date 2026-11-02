# AGENTS.md — คู่มือสำหรับ AI agent ที่ทำงานใน thaistt

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
python3 thaistt.py --help                       # CLI (ไฟล์เดียว → SRT · โฟลเดอร์/หลายไฟล์ → batch)
```

- งานหนักจริง (whisper.cpp, ffmpeg) เป็น **external binary** — หาผ่าน args หรือ env
  `THAISTT_WHISPER` / `THAISTT_MODEL` / `THAISTT_FFMPEG` หรือ PATH

## โครงสร้าง

| ที่อยู่ | หน้าที่ |
|---|---|
| `thaistt.py` | CLI เดียวจบ (`VERSION` ในไฟล์): ไฟล์เดียว ffmpeg → whisper.cpp → ชั้นจัดจังหวะซับ → SRT/txt · batch รายคลิป multi `-f` รอบเดียวต่อกลุ่ม → `<stem>.txt` + manifest CSV (avg_p · lang · suspicious — รวมตาม id ข้ามรอบ) · `--files-from`/`--force`/`--detect-lang`/`-l en,pl` |
| `install.sh` | symlink คำสั่งสั้น `thaistt` เข้า PATH (whitelist ปลายทาง) |
| `tests/` | unit (ชั้นจัดจังหวะใช้ SRT ดิบจำลองได้ ไม่ต้องมี whisper) + integration ด้วย `tests/fakes/` (CI ไม่ต้องมี whisper) + fixture สื่อจริง (skip เมื่อเครื่องไม่ครบ) |
| `NOTICE` | เครดิตส่วนประกอบบุคคลที่สาม — ต้องรักษาไว้ทุกครั้งที่แก้ |
| `CLAUDE.md` | บรรทัดเดียว `@AGENTS.md` ให้ Claude Code โหลดไฟล์นี้อัตโนมัติ — แก้กฎที่ AGENTS.md เท่านั้น |
| `.handover/` | `HANDOVER.md` สถานะงาน · `GUIDE.md` แนวทางเขียน handover + เช็กลิสต์ก่อนจบ session |

## กฎเหล็ก

1. **Python stdlib-only** — ไม่เพิ่ม dependency ภายนอก (งานหนักอยู่ที่ external binary อยู่แล้ว)
2. **ห้าม commit โมเดล/ไบนารี/ไฟล์สื่อตัวอย่างขนาดใหญ่** — fixture เสียงสั้น ๆ หรือสร้างแบบ
   synthetic ด้วยสคริปต์แทน
3. **CLI contract ตามสเปก workspace** — อาร์กิวเมนต์/exit codes (0 สำเร็จ · 2 พัง) ห้ามเปลี่ยน
   โดยไม่ประสานกับ `spec-vox-whisper.md` (ผู้บริโภคคือ TVox ใน THub-Launcher)
4. เทสใหม่ประกอบทุกฟีเจอร์/ทุก bugfix · CI (GitHub Actions) ต้องเขียวทุก push
5. จบ session ทำงาน → อัปเดต `.handover/HANDOVER.md` พร้อม commit hash เสมอ (ตามแนวทาง/เช็กลิสต์ใน `.handover/GUIDE.md`)
