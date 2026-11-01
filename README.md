# thaistt

ถอดเสียง/วิดีโอเป็น**ซับฉบับร่าง (SRT)** เพื่อส่งต่อเข้า pipeline แปลไทย — ด้วย
[whisper.cpp](https://github.com/ggml-org/whisper.cpp) + ffmpeg **รัน local ทั้งหมด**

สร้างซับร่างจากเสียงภาษาต่างประเทศ (อังกฤษ/ญี่ปุ่น ฯลฯ) พร้อมจังหวะเวลาที่จัดแล้วให้เหมาะเป็นซับ
แล้วส่งต่อให้ TStudio แปลไทยตาม pipeline ปกติ:

```
เสียง/วิดีโอ → thaistt → SRT ร่าง → [ตรวจแก้ต้นฉบับในตาราง] → แปล AI → QA → Thai Wrap → ซับไทย
```

## สถานะ

✅ **v0.1 พร้อมใช้** (Phase S0 ตามสเปก `../THub Studio/docs/spec-vox-whisper.md`)
· Python **stdlib-only** · รองรับ whisper.cpp หลาย build (ธง output สองสไตล์ probe อัตโนมัติ)

## การใช้งาน

```bash
python3 thaistt.py <media> -l <lang|auto> -o <draft.srt> \
    [--whisper <path>] [--model <path.ggml>] [--ffmpeg <path>] \
    [--format srt|txt] [--max-chars 42] [--max-dur 7] [--min-gap 0.8] \
    [--range SS.ss-SS.ss] [--prompt "ชื่อเฉพาะ..."] [-t <threads>] [--quiet]
```

- รับไฟล์ทั่วไปที่ ffmpeg รองรับ (mp4/mkv/wav/ogg/mp3/m4a/webm ฯลฯ)
- ภายใน: ffmpeg → WAV 16kHz mono (temp) → whisper.cpp → **ชั้นจัดจังหวะซับ**
  (merge ท่อนติดกันตราบใดที่ ≤ max-chars/max-dur · ช่องว่างเงียบ ≥ min-gap คือ
  ขอบซับ · ท่อนเดี่ยวที่ยาวเกินถูกซอยที่ช่องว่างระหว่างคำ · เลขลำดับเรียงใหม่)
- `--format txt` = ข้อความล้วนต่อไฟล์ (งานตรวจซับเทียบเสียงรายคลิป)
- `--range 10.5-42` = ถอดเฉพาะช่วง — **เวลาใน SRT ร่างอ้างตามไทม์ไลน์ต้นฉบับเสมอ**
- `--prompt` ส่งต่อให้ whisper (บอกชื่อเฉพาะ/ศัพท์เกมให้สะกดถูก)
- **ไม่แตะไฟล์ต้นฉบับ** · temp ล้างเองแม้รันพังกลางทาง
- exit `0` = สำเร็จ · `2` = พัง (stderr ไทยบอกสาเหตุ) · progress % ที่ stderr
  (`progress: NN% …`) เมื่อไม่ได้ใส่ `--quiet`
- path เครื่องมือ: อาร์กิวเมนต์ → env `THAISTT_WHISPER` / `THAISTT_MODEL` /
  `THAISTT_FFMPEG` → ค้น PATH · หาไม่เจอแจ้งชัด

ผู้บริโภคหลัก: **TVox ใน THub-Launcher** (Phase S2 — Settings `stt_cli_path` +
ปุ่มถอดเสียง) → SRT ร่างวิ่งเข้า Game Tool "Subtitles" ของ TFormat-Plugins

## การพัฒนา

อ่าน `AGENT.md` ก่อนแตะโค้ดทุกครั้ง · สถานะงานล่าสุดอยู่ที่ `.handover/HANDOVER.md`

```bash
python3 -m unittest discover -s tests -v   # ชุดจัดจังหวะ/CLI/integration (ไม่ต้องมี whisper)
THAISTT_TEST_MODEL=<path.ggml> python3 -m unittest tests.test_fixture_real -v  # fixture จริง (มีเครื่องมือครบเท่านั้น)
```

ห้าม commit โมเดล/ไบนารี/สื่อ (กฎ AGENT.md ข้อ 2) · เครดิตส่วนประกอบบุคคลที่สามดู `NOTICE`
