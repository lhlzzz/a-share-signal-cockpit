# T1: CDP Phase2 候选详情页 Tab 复用

## 目标
`collect_candidate_detail_evidence()` 当前为每个候选股票开一个新 CDP 标签页，导致盘中扫描时出现 20-30 个额外 tab，是卡顿的主要来源之一。
Phase 2 要求：复用已有 tab（优先用 required/enhanced 层中打开的 tab），导航到候选页面读取数据，再导航回原始 URL，整个过程不新增 tab。

## 文件范围（仅修改这些文件）
- `xiaogu_eastmoney_web_tabs_scan_v0_1.py`（主改动）
- `tests/test_xiaogu_a_share_forward_runner.py`（新增测试）

## 禁止修改
- `xiaogu_forward_d1_1450_runner_v0_1.py`
- `xiaogu_forward_result_filler_v0_1.py`
- `xiaogu_forward_paper_recorder_v0_1.py`
- `forward_paper_ledger_v0_1.jsonl`

## 实现要求

### 1. 函数签名扩展
```python
def collect_candidate_detail_evidence(
    candidates: list,
    cdp_url: str,
    *,
    reuse_tab_id: str | None = None,   # 新增：若提供则复用此 tab
    shared_url_to_tab: dict | None = None,  # 新增：跨 source 共享 tab map
) -> dict[str, list]:
```

### 2. 复用逻辑
- 优先从 `shared_url_to_tab` 中取一个空闲 tab（不在 required 关键路径上的 tab）
- 若找不到空闲 tab，再从 `cdp_page_tabs(cdp_url)` 中取第一个非 required URL 的 tab
- 用 `cdp_navigate_tab(cdp_url, url)` 导航到候选页面，读取数据，记录结果
- 读完后导航回原 URL（或 about:blank）
- 整个过程不调用 `open_cdp_tab()`
- 若无任何 tab 可复用才 fallback 到 open 新 tab（并在 evidence 中标注 `tab_reuse=False, fallback=True`）

### 3. 主流程接入
在 `xiaogu_eastmoney_web_tabs_scan_v0_1.py` main() 调用 `collect_candidate_detail_evidence` 时传入 `shared_url_to_tab=shared_url_to_tab`。

### 4. evidence 输出新增字段
每条 evidence 记录新增：
```python
{
  "tab_reuse": True,         # 是否复用了 tab
  "reused_tab_id": "...",    # 复用的 tab id
  "tabs_opened_for_detail": 0,  # 本次为候选详情新开的 tab 数
}
```

## 验收标准
1. `python3 -c "import xiaogu_eastmoney_web_tabs_scan_v0_1"` 无报错
2. 新增测试 `test_collect_candidate_detail_evidence_reuses_tab` 通过：
   - mock CDP 返回 2 个已有 tab
   - 调用 collect_candidate_detail_evidence，验证未调用 open_cdp_tab
   - evidence 中 `tab_reuse=True`
3. `python3 -m pytest tests/ -x -q` 所有测试通过（不允许跳过或 xfail）
4. `python3 -m py_compile xiaogu_eastmoney_web_tabs_scan_v0_1.py` 无错

## 注意
- 不要改变 evidence 的数据结构，只在每条 evidence 末尾追加 tab_reuse 元数据字段
- cdp_navigate_tab 调用后必须 sleep 至少 2s 等待页面加载
- required tab 的 URL（REQUIRED_CDP_TAB_URLS values）不得被导航覆盖
