# Scanner Skill

## Trigger

When market data collection is needed for A-share candidates.

## Workflow

1. Start CloakChrome CDP (port 9333)
2. Open required CDP tabs (行情中心, 资金流向, etc.)
3. Collect T-day visible data via DOM extraction
4. Score candidates using structured signals
5. Write evidence to `data/live_scan/`

## Key Files

- `xiaogu_eastmoney_web_tabs_scan_v0_1.py` — main scanner
- `xiaogu_cdp_tab_reuse.py` — CDP tab management
- `start_xiaogu_cdp_9333.sh` — CloakChrome launcher

## Verification

- All required tabs loaded (check `tab_status`)
- Candidate count > 0
- No DOM extraction errors
- Evidence files written

## Common Pitfalls

- CDP tabs not opened → use `--open-required-cdp-tabs`
- Anti-scraping blocked → must use CloakChrome, not bare Chrome
- Stale data → verify `source_time` is today
