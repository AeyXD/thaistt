# -*- coding: utf-8 -*-
"""smoke test ขั้นต่ำ: โมดูลหลัก parse ได้ — ชุดจริงอยู่ที่ test_timing/test_cli/
test_integration (fake binaries) และ test_fixture_real (ข้ามเมื่อไม่มี whisper จริง)"""
import ast
import unittest
from pathlib import Path


class TestSkeleton(unittest.TestCase):
    def test_module_parses(self):
        src = Path(__file__).resolve().parent.parent / "thaistt.py"
        ast.parse(src.read_text(encoding="utf-8"), filename=str(src))


if __name__ == "__main__":
    unittest.main()
