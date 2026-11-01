# thaistt

ถอดเสียง/วิดีโอเป็น**ซับฉบับร่าง (SRT)** เพื่อส่งต่อเข้า pipeline แปลไทย — ด้วย
[whisper.cpp](https://github.com/ggml-org/whisper.cpp) + ffmpeg **รัน local ทั้งหมด**

สร้างซับร่างจากเสียงภาษาต่างประเทศ (อังกฤษ/ญี่ปุ่น ฯลฯ) พร้อมจังหวะเวลาที่จัดแล้วให้เหมาะเป็นซับ
แล้วส่งต่อให้ TStudio แปลไทยตาม pipeline ปกติ:

```
เสียง/วิดีโอ → thaistt → SRT ร่าง → [ตรวจแก้ต้นฉบับในตาราง] → แปล AI → QA → Thai Wrap → ซับไทย
```

## สถานะ

🚧 **v0.1 กำลังเริ่ม** (สร้าง 2026-11-01 · skeleton + เอกสารประตูพร้อม ยังไม่มี implementation)
สเปกฉบับเต็ม: `../THub Studio/docs/spec-vox-whisper.md` → หัวข้อ "Phase S0"

## เป้าหมายการใช้งาน (v0.1)

```bash
thaistt <media> -l <lang|auto> -o <draft.srt> [--max-chars 42] [--max-dur 7] [--quiet]
```

- รับไฟล์ทั่วไปที่ ffmpeg รองรับ (mp4/mkv/wav/ogg/mp3/m4a/webm)
- จัดจังหวะซับให้เหมาะอ่าน: merge ช่วงสั้นติดกัน / split ตรงช่องว่างเงียบ
- ไม่แตะไฟล์ต้นฉบับ · exit 0 = สำเร็จ · 2 = พัง (stderr ภาษาไทย)

## การพัฒนา

อ่าน `AGENT.md` ก่อนแตะโค้ดทุกครั้ง · สถานะงานล่าสุดอยู่ที่ `.handover/HANDOVER.md` ·
Python **stdlib-only** · ห้าม commit โมเดล/ไบนารี (แค่ระบุ path ผ่าน args/env)
