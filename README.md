# thaistt

ถอดเสียง/วิดีโอเป็น**ซับฉบับร่าง (SRT)** เพื่อส่งต่อเข้า pipeline แปลไทย — ด้วย
[whisper.cpp](https://github.com/ggml-org/whisper.cpp) + ffmpeg **รัน local ทั้งหมด**

สร้างซับร่างจากเสียงภาษาต่างประเทศ (อังกฤษ/ญี่ปุ่น ฯลฯ) พร้อมจังหวะเวลาที่จัดแล้วให้เหมาะเป็นซับ
แล้วส่งต่อให้ TStudio แปลไทยตาม pipeline ปกติ:

```
เสียง/วิดีโอ → thaistt → SRT ร่าง → [ตรวจแก้ต้นฉบับในตาราง] → แปล AI → QA → Thai Wrap → ซับไทย
```

## สถานะ

✅ **v0.5 พร้อมใช้** — v0.1 (Phase S0) + **batch รายคลิป** v0.2 + v0.3 (manifest ·
ความมั่นใจ avg_p · .mp2 · --files-from/--force · เตือน prompt ยาว ตามขอใช้จริง W2)
+ v0.4 `--detect-lang` ตรวจภาษาต่อคลิป (ข้อ 9) ·
**v0.5 หลายภาษาในคำสั่งเดียว `-l en,pl`** (ข้อ 7 ของ W2 — แปลงเสียงแชร์ ผลแยกโฟลเดอร์)
· Python **stdlib-only** · รองรับ whisper.cpp หลาย build (probe อัตโนมัติ)

## การใช้งาน — ไฟล์เดียว → ซับร่าง SRT

```bash
python3 thaistt.py <media> -l <lang|auto> -o <draft.srt> \
    [--whisper <path>] [--model <path.ggml>] [--ffmpeg <path>] \
    [--format srt|txt] [--max-chars 42] [--max-dur 7] [--min-gap 0.8] \
    [--range SS.ss-SS.ss] [--prompt "ชื่อเฉพาะ..."] [--prompt-file <ชื่อไฟล์>] \
    [-t <threads>] [--quiet]
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
  `THAISTT_FFMPEG` → ค้น PATH · โมเดลไม่ได้ระบุ → ค้น `~/.local/share/whisper-models`
  (มีไฟล์เดียวจึงใช้อัตโนมัติ) · หาไม่เจอแจ้งชัด

## การใช้งาน — batch รายคลิป (v0.2)

```bash
python3 thaistt.py <folder|ไฟล์หลายตัว> -l <lang> --format txt -o <outdir> \
    [--batch-size 250] [-t 8] [--prompt "..."] [--prompt-file <ชื่อไฟล์>] \
    [--parallel-ffmpeg 8] [--files-from <list.txt|->] [--force] \
    [--model <path>] [--quiet]
```

- **whisper-cli รอบเดียวต่อชุด** (multi `-f` · default 250 ไฟล์ — reuse การโหลดโมเดล
  ~8s ตามหลักฐาน W1: 2,474 คลิป/19 นาที แทนเรียกทีละไฟล์ ≈ 46 ชม.) · แปลง ffmpeg ขนาน
- ผลออก `<ชื่อไฟล์>.txt` รายคลิปใน outdir · **มีไฟล์อยู่แล้ว = ข้าม (resume ได้)**
- **สรุปผลทาง stderr เสมอแม้ --quiet**: สำเร็จ / ว่าง(ไม่มีเสียงพูด) / พัง / ข้าม
  + **คลิปน่าสงสัย** (สั้น < 1 วินาที หรือวลีหลอนอย่าง "Thank you." — พยาธิวิทยาจริงจาก W1)
  ให้คนตรวจรู้ก่อน
- ทนคลิปพังบางส่วน: คลิปที่ ffmpeg/whisper พังถูกแยกรายงาน **ไม่หยุดทั้งชุด**
  (ชุดใหญ่พัง → เรียกซ้ำรายไฟล์อัตโนมัติเพื่อแยกคลิปพิษ) · มีคลิปพัง = exit 2
- โฟลเดอร์ดูแบบแบน กรองเฉพาะนามสกุลสื่อ (ไฟล์ `.say`/ไฟล์ซ่อนไม่โดน · รับ `.mp2`
  ตามเสียง w2speech ของ W2) · ชื่อคลิป (ไม่รวมนามสกุล) ชนกัน → ไฟล์แรกใช้ ที่เหลือข้ามพร้อมรายงาน
- **manifest ต่อรอบ: `<outdir>/.thaistt-manifest.csv`** (utf-8-sig เปิดใน Excel ได้) —
  `id · file · status(ok/empty/fail/skip) · duration_s · avg_p · low_conf_pct ·
  lang · lang_p · lang_mismatch · suspicious · text_chars · note` — ฝั่งเกมไม่ต้อง
  เดาความยาวจากขนาดไฟล์ และเรียง `avg_p` น้อย→มากได้ลำดับ "ตรวจก่อน" ทันที
  (ค่าความมั่นใจมาจาก `-ojf` ของ whisper.cpp — token ละ p · build ไม่รองรับ = คอลัมน์ว่าง)
- **หลายภาษาในคำสั่งเดียว**: `-l en,pl` — แปลง WAV **ครั้งเดียวแชร์ทุกภาษา**
  → whisper รอบเดียวต่อ (กลุ่ม×ภาษา) → ผลแยกโฟลเดอร์ `<outdir>/en/`, `<outdir>/pl/`
  + manifest ต่อภาษา (W2: เทียบเสียงโปแลนด์ต้นฉบับกับคำแปล) · ภาษาเดียวยังออกที่
  `<outdir>` ราบเหมือนเดิม (เข้ากับงาน W1 เดิม) · `auto` รวมในลิสต์ไม่ได้
- **`--detect-lang`**: ตรวจภาษาที่ได้ยินต่อคลิปอีกรอบ (รอบเดียวต่อกลุ่ม จบหลัง
  langid ไม่ decode — เร็ว) → คอลัมน์ `lang`/`lang_p`/`lang_mismatch` ใน manifest —
  จับคลิปที่น่าจะ**ไม่ใช่ภาษาที่สั่ง** (ภาษาเอลฟ์/เสียงร้อง/คาถา ตามข้อ 9 ของ W1/W2 —
  พิสูจน์ด้วยคลิปเยอรมันจริง: สั่ง `-l en` แล้วตรวจพบ `de (p=0.99)` ติด mismatch)
- `--files-from list.txt` (หรือ `-` = stdin · บรรทัดละ path · `#` คอมเมนต์) — เลือก id
  เฉพาะที่ต้องการโดยไม่ต้องแยกโฟลเดอร์ต่อองก์ · `--force` ถอดซ้ำแม้มีผลแล้ว
  (เช่นหลังเพิ่มชื่อใน --prompt-file แล้วอยากถอดบรรทัดที่ชื่อเพี้ยนใหม่)
- `--prompt`/`--prompt-file` ยาวเกิน ~224 token (≈896 ตัวอักษร) → เตือนทันที
  (whisper ตัดส่วนเกินเงียบ ๆ — จัดชื่อสำคัญไว้ต้นไฟล์)

ตัวอย่างผลจริง (W1 voices 20 คลิป, large-v3-turbo, M-series — 9.6 วิ):

```
สรุป batch: ทั้งหมด 20 · สำเร็จ 20 · ว่าง 0 · พัง 0 · ข้าม 0
คลิปน่าสงสัย (ตรวจก่อน) 3:
  11660.ogg (0.9s) "Right."
  15406.ogg (0.7s) "Dandelion"
  18644.ogg (0.7s) "Thanks."
```

ผู้บริโภคหลัก: **TVox ใน THub-Launcher** (Phase S2 — Settings `stt_cli_path` +
ปุ่มถอดเสียง) → SRT ร่างวิ่งเข้า Game Tool "Subtitles" ของ TFormat-Plugins

## ติดตั้งเป็นคำสั่งสั้น `thaistt`

```bash
./install.sh                # symlink ไป /opt/homebrew/bin หรือ /usr/local/bin (ไม่ต้อง sudo)
./install.sh --dest ~/.local/bin   # ระบุเอง
./install.sh --uninstall    # ถอด
```

เป็น symlink เข้าโฟลเดอร์นี้ (ไม่คัดลอก) — `git pull` แล้วได้รุ่นใหม่ทันที

## การพัฒนา

อ่าน `AGENT.md` ก่อนแตะโค้ดทุกครั้ง · สถานะงานล่าสุดอยู่ที่ `.handover/HANDOVER.md`

```bash
python3 -m unittest discover -s tests -v   # ชุดจัดจังหวะ/CLI/integration (ไม่ต้องมี whisper)
THAISTT_TEST_MODEL=<path.ggml> python3 -m unittest tests.test_fixture_real -v  # fixture จริง (มีเครื่องมือครบเท่านั้น)
```

ห้าม commit โมเดล/ไบนารี/สื่อ (กฎ AGENT.md ข้อ 2) · เครดิตส่วนประกอบบุคคลที่สามดู `NOTICE`
