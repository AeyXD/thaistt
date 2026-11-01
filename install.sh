#!/bin/sh
# install.sh — ติดตั้งคำสั่งสั้น "thaistt" ให้เรียกได้จากทุกที่ (ตามที่ ThaiW1Setup ฝาก)
#
#   ./install.sh              ติดตั้ง (เลือกปลายทางอัตโนมัติ: โฟลเดอร์ที่อยู่ใน PATH และเขียนได้)
#   ./install.sh --dest DIR   ระบุปลายทางเอง เช่น ~/.local/bin
#   ./install.sh --uninstall  ถอดการติดตั้ง
#
# วิธีทำงาน: symlink ไปที่ thaistt.py ในโฟลเดอร์นี้ (ไม่คัดลอก — git pull แล้วได้รุ่นใหม่ทันที)
set -eu

here=$(cd "$(dirname "$0")" && pwd)
target="$here/thaistt.py"
name="thaistt"

unlink_cmd() {
  for dest in "$@"; do
    link="$dest/$name"
    if [ -L "$link" ] && [ "$(readlink "$link")" = "$target" ]; then
      rm "$link"
      echo "ถอดแล้ว: $link"
    fi
  done
}

if [ "${1:-}" = "--uninstall" ]; then
  unlink_cmd /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin" "${2:-}"
  echo "เสร็จ (ถอดการติดตั้ง)"
  exit 0
fi

if [ ! -f "$target" ]; then
  echo "ไม่พบ $target — รันสคริปต์นี้จากโฟลเดอร์ thaistt" >&2
  exit 1
fi

# ธง executable ของไฟล์ต้นทาง (git เก็บสถานะนี้ไว้ด้วย)
chmod +x "$target"

dest=""
if [ "${1:-}" = "--dest" ] && [ -n "${2:-}" ]; then
  dest="$2"
else
  # เลือกเองจาก whitelist ที่ปลอดภัยเท่านั้น (ห้ามเลือก bin ของ version manager
  # เช่น sdkman — current ถูกสลับแล้ว symlink หาย) · ต้องเขียนได้ไม่ต้อง sudo
  for dir in /opt/homebrew/bin /usr/local/bin; do
    if [ -d "$dir" ] && [ -w "$dir" ]; then
      dest="$dir"
      break
    fi
  done
  if [ -z "$dest" ]; then
    dest="$HOME/.local/bin"
    mkdir -p "$dest"
    echo "หมายเหตุ: ไม่พบ /opt/homebrew/bin หรือ /usr/local/bin ที่เขียนได้ — ใช้ $dest" >&2
    echo "ถ้า $dest ยังไม่อยู่ใน PATH ให้เพิ่ม: export PATH=\"\$HOME/.local/bin:\$PATH\"" >&2
  fi
fi

mkdir -p "$dest" 2>/dev/null || true
ln -sf "$target" "$dest/$name"
echo "ติดตั้งแล้ว: $dest/$name -> $target"
echo "ทดสอบ: $name --help"
