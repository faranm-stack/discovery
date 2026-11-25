"""Local black-box regressions; no model, cloud connection or image required."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
EXAMPLE = json.loads((HERE / "example.json").read_text(encoding="utf-8"))
CASES = json.loads((HERE / "checks.json").read_text(encoding="utf-8"))


class ReferenceActionTests(unittest.TestCase):
    def run_case(self, request, success, matches=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "request.json").write_text(json.dumps(request), encoding="utf-8")
            proc = subprocess.run([sys.executable, "-B", str(HERE / "action.py"), "--request",
                                   str(root / "request.json"), "--output", str(root / "out")],
                                  capture_output=True, text=True, timeout=15, cwd=root)
            self.assertEqual(proc.returncode, 0 if success else 2, proc.stderr+proc.stdout)
            result = json.loads((root / "out" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "completed" if success else "failed")
            self.assertEqual(json.loads(proc.stdout)["status"], result["status"])
            if not success:
                self.assertNotIn("result", result)
                self.assertTrue(result["error"]["code"])
                return
            self.assertEqual(result["operation"], EXAMPLE["operation"])
            for key, expected in matches.items():
                actual = result["result"]
                for part in key.split("."):
                    actual = actual[part]
                if type(expected) in (list, int, float):
                    np.testing.assert_allclose(actual, expected, rtol=1e-10, atol=1e-12)
                else:
                    self.assertEqual(actual, expected)

    def test_reference(self):
        self.run_case(EXAMPLE, True, CASES["checks"])

    def test_boundary(self):
        self.run_case({**EXAMPLE, **CASES["boundary"]}, True, CASES["boundary_checks"])

    def test_invalid(self):
        self.run_case({**EXAMPLE, **CASES["invalid"]}, False)

    def test_wrong_operation(self):
        # Pick a real other operation, with its valid inputs, not a random typo.
        other = ({"operation":"active_space","orbitals":1,"electrons":1,"twice_spin":1}
                 if EXAMPLE["operation"] != "active_space" else
                 {"operation":"error_correction","distance":1,"bit_flip_probability":0.1})
        self.run_case(other, False)


if __name__ == "__main__":
    unittest.main()