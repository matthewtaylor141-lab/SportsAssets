#!/usr/bin/env python3
from pathlib import Path
import os,sys,subprocess
ROOT=Path(__file__).resolve().parent
suites=[
 ("golden",ROOT/"golden_market_validation"/"tests",ROOT/"golden_market_validation"),
 ("scoreboard",ROOT/"forward_profitability_scoreboard"/"tests",ROOT/"forward_profitability_scoreboard"),
 ("acceptance",ROOT/"final_pm_acceptance_harness"/"tests",ROOT/"final_pm_acceptance_harness"),
]
for name,tests,py in suites:
    print(f"=== {name} ===")
    env=os.environ.copy()
    env["PYTHONPATH"]=str(py)
    r=subprocess.run([sys.executable,"-m","pytest","-q",str(tests)],env=env)
    if r.returncode: raise SystemExit(r.returncode)
print("ALL 45 PACKAGE TESTS GREEN")
