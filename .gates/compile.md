# Compile Gate

## Before merge

- [ ] All Python files import without error
- [ ] No syntax errors (`python3 -m py_compile <file>`)
- [ ] No undefined name errors
- [ ] No circular imports
- [ ] Requirements.txt up to date

## Check

```bash
python3 -c "import xiaogu_forward_d1_1450_runner_v0_1"
python3 -c "import xiaogu_eastmoney_web_tabs_scan_v0_1"
python3 -c "import xiaogu_forward_result_filler_v0_1"
python3 -c "import xiaogu_api"
python3 -c "import xiaogu_db"
```
