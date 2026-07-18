# xiaogu TOOLING

共享工具入口：`/root/hermes/company-ai-system/tools/external/TOOLING.md`

## 可用工具

- Backtrader 源码：`/root/hermes/company-ai-system/tools/external/repos/backtrader`
- vectorbt 源码：`/root/hermes/company-ai-system/tools/external/repos/vectorbt`
- qlib 源码：`/root/hermes/company-ai-system/tools/external/repos/qlib`
- vn.py 源码：`/root/hermes/company-ai-system/tools/external/repos/vnpy`（source-only / research-only；不得使用 broker、gateway、account、order endpoint 或自动交易能力）
- QuantConnect Lean 源码：`/root/hermes/company-ai-system/tools/external/repos/Lean`
- QuantDinger 源码：`/root/hermes/company-ai-system/tools/external/repos/QuantDinger`（research-only；禁止使用 paper order、live trading、broker/exchange credentials、order endpoint 或 ledger write 能力）
- Python 入口：`/root/hermes/company-ai-system/tools/external/bin/quant-python`
- Lean CLI 入口：`/root/hermes/company-ai-system/tools/external/bin/lean`

## Smoke

```bash
/root/hermes/company-ai-system/tools/external/bin/quant-python -c "import backtrader, vectorbt, qlib; print('quant ok')"
test -d /root/hermes/company-ai-system/tools/external/repos/vnpy/vnpy
```

## 使用规则

- 仅用于研究、回测框架、参数扫描和 PAPER_ONLY / NO_TRADE 策略验证。
- A 股链路固定口径：东财=稳定行情固定源，Qlib=研究特征，vectorbt=策略演化，Lean=paper/backtest replay gate，QuantDinger=research-only / data-health / liquidity guard；Lean 缺 Docker/dotnet 本地运行时或无实际 engine replay 证据时必须 fail-closed。
- 回测 evidence 不等于交易建议，不得影响真实交易或晋级。
- 不要把 Python 依赖或外部 repo 装进本 workspace。
- 使用结果写入 `RESEARCH.md` 或 `SESSION.md`。

## 通用浏览器 / 评论资料工具

- 东财正式出票链路固定为一个专用 CDP：端口 `9333` + profile `/root/.claude/browser-profiles/xiaogu/cdp-debug`；启动：`bash start_xiaogu_cdp_9333.sh` 或 `hermes-cdp xiaogu`；扫描脚本只连接 `--cdp-url http://127.0.0.1:9333`；新 CDP/缺页签时加 `--open-required-cdp-tabs` 自动补齐东财 required tabs。
- 当前项目浏览器 MCP：`playwright-xiaogu` / `chrome-devtools-xiaogu`；只作为非正式浏览器辅助或 CDP 9333 不可用时的备选，不要在东财正式出票链路里用它们探测页签。
- 注意：`chrome-devtools-xiaogu` 使用独立 profile `/root/.claude/browser-profiles/xiaogu/chrome-devtools`，会启动 about:blank 空实例；`playwright-xiaogu` 也使用独立 profile `/root/.claude/browser-profiles/xiaogu/playwright`。正式 A 股链路只用 `cdp-debug`，不得与这些实例/profile 混用。
- 当前项目浏览器输出：`/root/.claude/browser-output/xiaogu/`。
- 非 A 股研究入口归属 `xiaomei`；当前 `xiaogu` 只保留 `MISSING_CANONICAL_US_SCANNER` 边界提示，不在 A 股 stable chain 中调用相关 scanner/replay。
- MediaCrawler：`/root/hermes/company-ai-system/tools/external/bin/mediacrawler --help`，仅用于授权/公开范围内的评论、社媒讨论和舆情采集候选。
- CloakBrowser：`cloakbrowser info`，用于稳定浏览器自动化环境；不得用于自动绕过验证码、滑块、短信、人脸或其他平台人机验证。
- 遇到验证码、滑块、短信、人脸或其他人机验证时，停止自动化并请求用户手动处理；完成后可继续自动化，或改用平台允许的官方接口/导出。
