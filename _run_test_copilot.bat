@echo off
cd /d "c:\Users\TA29225\Spec AI Project"
.venv\Scripts\python.exe -u _test_copilot_fix_v2.py > _test_copilot_out.txt 2>&1
type _test_copilot_out.txt
