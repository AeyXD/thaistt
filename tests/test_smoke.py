# -*- coding: utf-8 -*-
"""smoke test ขั้นต่ำของ skeleton — S0 จะแทนที่ด้วยชุดทดสอบจริง (ชั้นจัดจังหวะซับ ฯลฯ)"""
import ast
import unittest
from pathlib import Path


class TestSkeleton(unittest.TestCase):
    def test_module_parses(self):
        src = Path(__file__).resolve().parent.parent / "thaistt.py"
        ast.parse(src.read_text(encoding="utf-8"), filename=str(src))


if __name__ == "__main__":
    unittest.main()
