"""Run the 2025 regression suite without pytest.

The suite in jdcvo/test_2025_validation.py is plain functions with asserts and
no fixtures, so it runs fine with a bare collector. pytest isn't installed on
this machine; this keeps the regression check one command away.

Usage: python3 tools/run_2025_tests.py
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jdcvo import test_2025_validation, test_tiebreak

suites = [test_2025_validation, test_tiebreak]
tests = [(s, n) for s in suites for n in sorted(dir(s))
         if n.startswith('test_')]
failed = []

for suite, name in tests:
    try:
        getattr(suite, name)()
        print(f"PASS  {name}")
    except Exception:
        failed.append(name)
        print(f"FAIL  {name}")
        traceback.print_exc()

print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
sys.exit(1 if failed else 0)
