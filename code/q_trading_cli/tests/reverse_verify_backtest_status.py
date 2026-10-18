#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-09-17
Description: 反向验证驱动 —— 把页面 / 路由 / main.py 逐个改坏，确认冒烟检查真的会报错。

「全绿」本身没有信息量：断言可能只是恰好成立（比如断言写成了"文件里出现过某个字符串"）。
因此每次写完断言都要跑一遍本脚本：每个用例改坏一处**该被断言抓住**的逻辑，
子进程重跑冒烟检查，要求它 exit != 0 且报出了预期的那条断言。

覆盖四个检查：
    - tests/strategy_backtest_smokecheck.py （SMOKE_PAGE_SRC / SMOKE_ROUTES_SRC / SMOKE_MAIN_SRC）
    - tests/strategies_dialog_smokecheck.py （SMOKE_STRATEGIES_SRC）
    - tests/strategy_monitor_smokecheck.py （SMOKE_MONITOR_PAGE_SRC）
    - tests/main_page_smokecheck.py       （SMOKE_MAIN_PAGE_SRC / SMOKE_HOLDINGS_SRC）

运行：python tests/reverse_verify_backtest_status.py   （exit 0 = 全部用例都被抓到）
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent
TMP: pathlib.Path = pathlib.Path(tempfile.mkdtemp(prefix="rev_status_"))

# 用当前解释器跑子进程（本脚本可能由别的 python 调起）
PYTHON: str = sys.executable

BACKTEST_CHECK = ROOT / "tests" / "strategy_backtest_smokecheck.py"
DIALOG_CHECK = ROOT / "tests" / "strategies_dialog_smokecheck.py"
MONITOR_CHECK = ROOT / "tests" / "strategy_monitor_smokecheck.py"
MAIN_PAGE_CHECK = ROOT / "tests" / "main_page_smokecheck.py"
PREFERENCES_CHECK = ROOT / "tests" / "settings_preferences_smokecheck.py"
LOGIN_CHECK = ROOT / "tests" / "login_page_smokecheck.py"

BACKTEST_PAGE = ROOT / "pages" / "strategy_backtest.py"
DIALOG_PAGE = ROOT / "pages" / "strategies.py"
MONITOR_PAGE = ROOT / "pages" / "strategy_monitor.py"
MAIN_PAGE = ROOT / "pages" / "main_page.py"
HOLDINGS = ROOT / "core" / "holdings.py"
API_USER_STRATEGY = ROOT / "api" / "user_strategy.py"
SIGNAL_MGR = ROOT / "mq" / "signal_manager.py"
ROUTES = ROOT / "core" / "routes.py"
MAIN = ROOT / "main.py"
PREFERENCES_PAGE = ROOT / "pages" / "settings_preferences.py"
LOGIN_PAGE = ROOT / "pages" / "login_page.py"
API_CLIENT = ROOT / "api" / "client.py"

# 每个用例：(用例名, 源文件, env 变量名, [(旧片段, 新片段), ...], 期望在输出里出现的关键词)
CASES: list[tuple[str, pathlib.Path, str, list[tuple[str, str]], list[str]]] = [
    # ---- 回测页：返回按钮跟随入口 ----
    (
        "返回按钮写死父页面（从策略列表进来也回策略监控）",
        BACKTEST_PAGE, "SMOKE_PAGE_SRC",
        [('        ui.navigate.to(back_route)',
          '        ui.navigate.to(route_for_page_key("monitor"))')],
        ["从策略列表进来时返回到 /strategies"],
    ),
    (
        "返回按钮文案写死「返回策略监控」",
        BACKTEST_PAGE, "SMOKE_PAGE_SRC",
        [('    back_label: str = _BACK_LABELS.get(back_key, _BACK_LABELS[DEFAULT_BACKTEST_SOURCE])',
          '    back_label: str = "返回策略监控"')],
        ["从策略列表进来时按钮文案为「返回策略列表」"],
    ),
    (
        "空态（未指定策略）的返回按钮写死父页面",
        BACKTEST_PAGE, "SMOKE_PAGE_SRC",
        [('            ui.button(back_label, icon="arrow_back", on_click=_go_back).props(\n'
          '                "flat no-caps"\n'
          '            ).style(f"color: {link_color}; margin-top: 12px;")',
          '            ui.button("返回策略监控", icon="arrow_back", on_click=_go_back).props(\n'
          '                "flat no-caps"\n'
          '            ).style(f"color: {link_color}; margin-top: 12px;")')],
        ["未指定策略的空态下所有返回入口都跟随来源（没有写死的）"],
    ),
    # ---- 路由：来源白名单与 URL 构造 ----
    (
        "backtest_source 不做白名单校验（原样返回）",
        ROUTES, "SMOKE_ROUTES_SRC",
        [('    key: str = str(source or "").strip()\n'
          '    return key if key in BACKTEST_SOURCE_KEYS else DEFAULT_BACKTEST_SOURCE',
          '    return str(source or "").strip() or DEFAULT_BACKTEST_SOURCE')],
        ["来源白名单：空值 / 非法值回退 monitor"],
    ),
    (
        "backtest_route 丢掉 source 查询参数",
        ROUTES, "SMOKE_ROUTES_SRC",
        [('    params[BACKTEST_SOURCE_PARAM] = backtest_source(source)',
          '    pass')],
        ["backtest_route 用查询参数传策略 ID 与来源"],
    ),
    (
        "backtest_route 空 ID 时不带来源",
        ROUTES, "SMOKE_ROUTES_SRC",
        [('    params[BACKTEST_SOURCE_PARAM] = backtest_source(source)',
          '    if sid:\n        params[BACKTEST_SOURCE_PARAM] = backtest_source(source)')],
        ["backtest_route 空 ID 时仍带来源"],
    ),
    (
        "backtest_route 的 source 默认值改成 strategies",
        ROUTES, "SMOKE_ROUTES_SRC",
        [('DEFAULT_BACKTEST_SOURCE: str = "monitor"',
          'DEFAULT_BACKTEST_SOURCE: str = "strategies"')],
        ["来源白名单：空值 / 非法值回退 monitor"],
    ),
    # ---- main.py：参数传递与侧边栏高亮 ----
    (
        "main.py 不再把 source 传给页面内容函数",
        MAIN, "SMOKE_MAIN_SRC",
        [('            partial(_show, strategy_id, source=back_key), page_key=back_key',
          '            partial(_show, strategy_id), page_key=back_key')],
        ["查询参数被传给页面内容函数（来源已归一化）"],
    ),
    (
        "侧边栏高亮写死为 monitor（不跟随入口）",
        MAIN, "SMOKE_MAIN_SRC",
        [('            partial(_show, strategy_id, source=back_key), page_key=back_key',
          '            partial(_show, strategy_id, source=back_key), page_key="monitor"')],
        ["侧边栏高亮跟随入口（不再是写死的 monitor）"],
    ),
    (
        "页函数签名不再接收 source",
        MAIN, "SMOKE_MAIN_SRC",
        [("def strategy_backtest_page_view(strategy_id: str = '', source: str = ''):",
          "def strategy_backtest_page_view(strategy_id: str = ''):")],
        ["页函数接收查询参数 strategy_id 与 source"],
    ),
    (
        "新增未登记白名单的页面路径（模拟漏登记）",
        MAIN, "SMOKE_MAIN_SRC",
        [("    @ui.page('/strategy-backtest')",
          "    @ui.page('/strategy-backtest-new')\n    def _unregistered_dummy():\n        return None\n\n    @ui.page('/strategy-backtest')")],
        ["main.py 注册的每个页面路径都在白名单里"],
    ),
    (
        "监控页入口不带来源（回测页无从得知入口）",
        ROOT / "pages" / "strategy_monitor.py", "SMOKE_MONITOR_SRC",
        [('ui.navigate.to(backtest_route(sid, "monitor"))',
          'ui.navigate.to(backtest_route(sid))')],
        ["策略监控页用 backtest_route 构造跳转并带 source=monitor"],
    ),
    (
        "策略列表页入口不带来源",
        ROOT / "pages" / "strategies.py", "SMOKE_STRATEGIES_SRC",
        [('ui.navigate.to(backtest_route(sid, "strategies"))',
          'ui.navigate.to(backtest_route(sid))')],
        ["策略列表页入口用 backtest_route 并带 source=strategies"],
    ),
    # ---- 状态：创建对话框「立即运行」 ----
    (
        "「立即运行」默认不勾选",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('ui.checkbox("立即运行", value=True)', 'ui.checkbox("立即运行", value=False)')],
        ["「立即运行」默认勾选（value=True）"],
    ),
    (
        "创建时状态写死 stopped（勾选也创建为停止）",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('                        create_status: str = (\n'
          '                            "running" if run_now_checkbox.value else "stopped"\n'
          '                        )',
          '                        create_status: str = "stopped"')],
        ["默认勾选时 create 以 status=running 提交"],
    ),
    (
        "创建时状态写死 running（取消勾选也创建为运行）",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('                        create_status: str = (\n'
          '                            "running" if run_now_checkbox.value else "stopped"\n'
          '                        )',
          '                        create_status: str = "running"')],
        ["取消勾选时 create 以 status=stopped 提交"],
    ),
    (
        "后端已回填 status 时被前端选择覆盖",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('                            if not str(result.get("status", "") or "").strip():\n'
          '                                result["status"] = create_status',
          '                            result["status"] = create_status')],
        ["缓存里保留后端返回的 error（不被前端选择覆盖）"],
    ),
    (
        "以 running 创建时不写 update_time",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('                            if create_status == "running" and not result.get("update_time"):\n'
          '                                # 监控页的「已运行」以 update_time 起算，就地补一个现在时刻\n'
          '                                result["update_time"] = cur_date_time_str()',
          '                            pass')],
        ["以 running 创建时就地写入 update_time"],
    ),
    (
        "创建成功提示丢掉「已开始运行」",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('                            "创建成功，策略已开始运行"\n'
          '                            if create_status == "running"\n'
          '                            else "创建成功",',
          '                            "创建成功",')],
        ["创建成功给出提示（并说明已开始运行）"],
    ),
    # ---- 状态：编辑对话框 ----
    (
        "编辑保存不传 status（状态切换无效）",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('                                pool_id=pool_id,\n'
          '                                status=new_status,',
          '                                pool_id=pool_id,')],
        ["update 带上了新状态 paused"],
    ),
    (
        "状态下拉的默认值写死 stopped（不取当前状态）",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('                                        ui.select(options=status_options, value=current_status)',
          '                                        ui.select(options=status_options, value="stopped")')],
        ["状态下拉默认选中当前状态（stopped）"],
    ),
    (
        "「异常」不补进下拉选项（下拉框会空白）",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('        if current_status not in status_options:',
          '        if False:')],
        ["「异常」被临时补进选项"],
    ),
    (
        "把「异常」当成用户可选项",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('_EDITABLE_STATUS_KEYS: tuple[str, ...] = ("running", "paused", "stopped")',
          '_EDITABLE_STATUS_KEYS: tuple[str, ...] = ("running", "paused", "stopped", "error")')],
        ["「异常」不作为用户可选项（后端故障态）"],
    ),
    (
        "状态变更后不刷新 update_time",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('                            if status_changed:\n'
          '                                # 监控页的「已运行」以 user_strategy.update_time 起算。',
          '                            if False:\n'
          '                                # 监控页的「已运行」以 user_strategy.update_time 起算。')],
        ["状态变更时就地刷新 update_time"],
    ),
    (
        "状态没变也刷新 update_time（无意义改写）",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('                            if status_changed:',
          '                            if True:')],
        ["状态没变 ⇒ 不写 update_time"],
    ),
    (
        "编辑保存后不写会话缓存（列表状态不更新）",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('                            UserStrategyManager().update_user_strategy(row["us_id"], cache_patch)',
          '                            pass')],
        ["会话缓存同步为 paused"],
    ),
    # ---- 缓存写回形状：展示行数据 vs 缓存对象 ----
    (
        "编辑保存把展示行数据整份写回缓存（total_profit 被覆盖成 '0.0%'）",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('                            UserStrategyManager().update_user_strategy(row["us_id"], cache_patch)',
          '                            UserStrategyManager().update_user_strategy(row["us_id"], row)')],
        ["缓存写入不含展示专用键"],
    ),
    (
        "缓存写入里混进展示专用键 total_profit",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('                                "strategy_params": params,\n'
          '                            }',
          '                                "strategy_params": params,\n'
          '                                "total_profit": total_profit_str,\n'
          '                            }')],
        ["缓存写入不含展示专用键"],
    ),
    (
        "列表页 _load_rows 用 float() 而不是容错解析（脏缓存直接炸）",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('            total_profit: float = tools.to_float(us.get("total_profit", 0.0))',
          '            total_profit: float = float(us.get("total_profit", 0.0))')],
        ["could not convert string to float"],
    ),
    # ---- 监控页卡片「浮动盈亏」= 已实现 + 未实现 ----
    (
        "监控页浮动盈亏丢掉已实现段（只算持仓未实现部分）",
        MONITOR_PAGE, "SMOKE_MONITOR_PAGE_SRC",
        [('                float_profit = tools.to_float(realized) + float(unrealized)',
          '                float_profit = float(unrealized)')],
        ["浮动盈亏含已实现段"],
    ),
    (
        "监控页已实现段优先取会话缓存 total_profit（常驻页面上该缓存一直冻结在进页那一刻）",
        MONITOR_PAGE, "SMOKE_MONITOR_PAGE_SRC",
        [('                realized: Any = snap.get("realized")\n'
          '                if realized is None:',
          '                realized: Any = us.get("total_profit", 0.0)\n'
          '                if realized is None:')],
        ["已实现段取执行记录"],
    ),
    (
        "监控页已实现段用裸 float() 解析回退值（共享缓存里的展示格式字符串直接炸页）",
        MONITOR_PAGE, "SMOKE_MONITOR_PAGE_SRC",
        [('                float_profit = tools.to_float(realized) + float(unrealized)',
          '                float_profit = float(realized) + float(unrealized)')],
        ["could not convert string to float"],
    ),
    (
        "监控页缺实时价时退回已实现段当浮动盈亏（残缺值被读成完整浮盈）",
        MONITOR_PAGE, "SMOKE_MONITOR_PAGE_SRC",
        [('            unrealized: float | None = snap.get("unrealized")\n'
          '            float_profit: float | None = None',
          '            unrealized: float | None = snap.get("unrealized")\n'
          '            float_profit: float | None = tools.to_float(us.get("total_profit", 0.0))')],
        ["缺实时价时也不只显示已实现段"],
    ),
    (
        "监控页把空持仓当成「算不出浮动盈亏」（已清仓策略丢掉已实现段）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('    total: float = 0.0\n'
          '    for pos in positions:',
          '    if not positions:\n'
          '        return None\n'
          '    total: float = 0.0\n'
          '    for pos in positions:')],
        ["已清仓"],
    ),
    (
        "监控页缺实时价时用剩余持仓凑浮动盈亏（会被读成全仓浮盈）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('        if code not in price_map:\n'
          '            return None',
          '        if code not in price_map:\n'
          '            continue')],
        ["缺实时价时浮动盈亏显示 --"],
    ),
    (
        "监控页未实现段取持仓快照 profit_amount（成交时点值当浮动盈亏）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('        total += (price_map[code] - cost_price) * quantity',
          '        total += to_float(pos.get("profit_amount", 0.0))')],
        ["浮动盈亏含已实现段"],
    ),
    # ---- 持仓信息「盈亏」列：率(额) 格式与口径 ----
    (
        "盈亏列退回只显示金额（丢掉百分率）",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('                profit_text: str = f"{profit_rate:+.2f}%({abs(profit_amount):.2f})"',
          '                profit_text: str = f"{profit_amount:+,.0f}"')],
        ["盈利行展示 +20.00%(2000.00)"],
    ),
    (
        "盈亏金额不取绝对值（亏损行金额多一个负号）",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('f"{profit_rate:+.2f}%({abs(profit_amount):.2f})"',
          'f"{profit_rate:+.2f}%({profit_amount:.2f})"')],
        ["亏损行展示 -10.00%(500.00)"],
    ),
    (
        "盈亏百分率改取快照字段 profit_rate（与实时价现价自相矛盾）",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('                    profit_rate: float = (\n'
          '                        0.0 if cost_price == 0.0\n'
          '                        else (current_price - cost_price) / cost_price * 100\n'
          '                    )',
          '                    profit_rate: float = float(pos.get("profit_rate", 0.0) or 0.0)')],
        ["盈利行展示 +20.00%(2000.00)"],
    ),
    (
        "收益率不防除零（成本价为 0 的持仓直接抛异常）",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('0.0 if cost_price == 0.0\n'
          '                        else (current_price - cost_price) / cost_price * 100',
          '(current_price - cost_price) / cost_price * 100')],
        ["ZeroDivisionError"],
    ),
    (
        "现价取持仓快照而非实时行情",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('                current_price: float = float(price_dict.get(code, 0.0) or 0.0)',
          '                current_price: float = float(pos.get("current_price", 0.0) or 0.0)')],
        ["现价取实时价而非持仓快照的 current_price"],
    ),
    (
        "缺实时价时仍按「0 价」算盈亏（把没有行情当成跌到 0）",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('                current_price: float = float(price_dict.get(code, 0.0) or 0.0)\n'
          '                if current_price > 0.0:',
          '                current_price: float = float(price_dict.get(code, 0.0) or 0.0)\n'
          '                if current_price >= 0.0:')],
        ["现价与盈亏均为 --"],
    ),
    (
        "盈亏列允许折行（「率(额)」在窄列里断成两行）",
        DIALOG_PAGE, "SMOKE_STRATEGIES_SRC",
        [('f"color: {profit_color}; flex: 1; text-align: right; white-space: nowrap;"',
          'f"color: {profit_color}; flex: 1; text-align: right;"')],
        ["盈亏列禁止折行"],
    ),
    # ---- 概览页口径（core/holdings.py + pages/main_page.py）----
    (
        "未实现盈亏恒为 0（浮盈段整段丢失）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('        total += (price_map[code] - cost_price) * quantity',
          '        total += 0.0')],
        ["us-1 已实现 3,000 + 未实现 200"],
    ),
    (
        "缺实时价时用剩余持仓凑数（部分盈亏被当成全仓）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('        if code not in price_map:\n            return None',
          '        if code not in price_map:\n            continue')],
        ["us-2 缺实时价"],
    ),
    (
        "行情返回 0 价被当成真实价格",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('        if code and px > 0:', '        if code:')],
        ["build_price_map 只认正价且有代码"],
    ),
    (
        "初始资金为 0 时不退化为持仓成本（与后端口径不一致）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('    if base > 0:\n        return base', '    if base >= 0:\n        return base')],
        ["base_amount 为 0 时退化为持仓成本合计"],
    ),
    (
        "缺 current_profit 时不返回 None（拿 0 当已实现）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('    if realized_raw is None or unrealized is None:',
          '    if unrealized is None:')],
        ["current_return_rate_pct 缺 current_profit"],
    ),
    (
        "当前收益率漏掉已实现段（只剩浮动盈亏）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('    return (to_float(realized_raw) + unrealized) / base * 100.0',
          '    return unrealized / base * 100.0')],
        ["current_return_rate_pct = (已实现+未实现)/初始资金×100 = 3.2"],
    ),
    (
        "分母为 0 时返回 0 而非 None（显示成 0% 而不是「-」）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('    if base <= 0:\n        return None', '    if base <= 0:\n        return 0.0')],
        ["current_return_rate_pct 分母为 0"],
    ),
    (
        "执行记录按 strategy_id（模板 ID）归并",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('        us_id: str = str(ex.get("user_strategy_id", "") or "")',
          '        us_id: str = str(ex.get("strategy_id", "") or "")')],
        ["latest_execution_map 按 user_strategy_id 归并"],
    ),
    (
        "取全局最早而不是最新的执行记录",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('        if latest is None or ex_time >= latest_time:',
          '        if latest is None or ex_time <= latest_time:')],
        ["latest_execution 取全局最新"],
    ),
    (
        "概要页表格列名回退成「收益率 (30d)」（名实不符，需求方已否掉）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('("状态", 1), ("收益率", 1),', '("状态", 1), ("收益率 (30d)", 1),')],
        ["表头 5 列、末列是「收益率」"],
    ),
    (
        "曲线末点不叠加未实现",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('    if last_rate_pct is not None and total_rates:\n'
          '        total_rates[-1] = round(float(last_rate_pct), 2)',
          '    pass')],
        ["总收益曲线 = 各策略收益率的平均值（窗口外策略沿用最近一次取值），末点 = 含浮盈均值"],
    ),
    (
        "曲线每个点都叠加未实现（历史点被污染）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('        total_rates[-1] = round(float(last_rate_pct), 2)',
          '        total_rates = [round(float(last_rate_pct), 2)] * len(total_rates)')],
        ["总收益曲线 = 各策略收益率的平均值（窗口外策略沿用最近一次取值），末点 = 含浮盈均值"],
    ),
    # ---- 曲线改版（2026-09-23 平均值口径 + 时间范围查询 + 单策略对比线；
    #      2026-09-30 一度改成求和，同日拍板改回平均值并排除零收益策略）----
    (
        "同一天同一策略的多条记录相加（累计快照被重复累加，曲线虚高）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('        bucket[us_id] = to_float(raw) * 100.0',
          '        bucket[us_id] = bucket.get(us_id, 0.0) + to_float(raw) * 100.0')],
        ["strategy_day_rates 同一天同一策略取**最后一条**记录"],
    ),
    (
        "同一天同一策略取先出现的那条（输入顺序决定结果，乱序数据会取错）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('        if us_id in bucket and at < times[us_id]:\n'
          '            continue',
          '        if us_id in bucket:\n'
          '            continue')],
        ["strategy_day_rates 同一天同一策略取**最后一条**记录",
         "strategy_day_rates 输入顺序不影响结果（乱序传入也取时间最大的那条）"],
    ),
    (
        "末点改用窗口内记录聚合（窗口外的策略被漏掉，均值随窗口变化）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('                average_current_rate_pct(all_execs, price_map),',
          '                average_current_rate_pct(window_execs, price_map),')],
        ["总收益曲线 = 各策略收益率的平均值（窗口外策略沿用最近一次取值），末点 = 含浮盈均值"],
    ),
    (
        "缺实时价的策略从聚合里剔除（均值少一票、数值偏一边）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('    raw: Any = record.get("current_return_rate")\n'
          '    if raw is None:\n'
          '        return None\n'
          '    return round(to_float(raw) * 100.0, 2)',
          '    return None')],
        ["current_rate_pct_with_fallback 缺实时价 ⇒ 退化成已实现口径 5.0"],
    ),
    (
        "单策略曲线不按 user_strategy_id 过滤（画成第二条总曲线）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('        single_execs = [\n'
          '            ex for ex in executions\n'
          '            if str(ex.get("user_strategy_id", "") or "") == select_us_id\n'
          '        ]',
          '        single_execs = list(executions)')],
        ["对比曲线按 user_strategy_id 过滤"],
    ),
    (
        "单策略曲线缺失日期补 0（在图上画出跌到 0 的假线）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('        out.append(round(level, 2) if level is not None else None)',
          '        out.append(round(level, 2) if level is not None else 0.0)')],
        ["strategy_levels_on_days 在它第一条记录之前 ⇒ None（不补 0）"],
    ),
    (
        "窗口基准改用今天（久未交易的策略会画出空图）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('                    last_execution_day(all_execs), days_limit,',
          '                    datetime.now().strftime("%Y-%m-%d"), days_limit,')],
        ["窗口 = 最后数据点往前 30 天"],
    ),
    (
        "时间范围查询用接口默认 limit（500，一年数据被静默截断）",
        API_USER_STRATEGY, "SMOKE_API_SRC",
        [('        limit: int = 10000,', '        limit: int = 500,')],
        ["limit 默认取满 10000"],
    ),
    (
        "对比曲线末点无条件覆盖（窗口内没数据也凭空多一个孤点）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('    if (select_last_rate_pct is not None and single_rates\n'
          '            and any(v is not None for v in single_rates)):',
          '    if select_last_rate_pct is not None and single_rates:')],
        ["不凭空补一个孤立末点"],
    ),
    (
        "下拉框默认选中第一个策略（未选策略时不该多画一条线）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('                    value=None,',
          '                    value=next(iter(us_options), None),')],
        ["下拉框选项 = 用户策略 ID → 名称（未选策略时默认空）"],
    ),
    (
        "所选策略窗口内无数据时仍追加空曲线（图例里留一个不存在的名字）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('            has_single: bool = bool(us_id) and any(v is not None for v in single_rates)',
          '            has_single: bool = bool(us_id)')],
        ["不追加空曲线"],
    ),
    (
        "对比曲线也铺渐变面积（两条面积互相遮盖）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('    if is_primary:\n        line["areaStyle"] = {',
          '    if True:\n        line["areaStyle"] = {')],
        ["不铺面积"],
    ),
    (
        "tooltip 写死两条曲线（单条时引用了不存在的 series 索引）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('        ["{b}"] + [f"{{a{i}}}: {{c{i}}}%" for i in range(len(series))]',
          '        ["{b}", "{a0}: {c0}%", "{a1}: {c1}%"]')],
        ["tooltip 模板按曲线数量拼"],
    ),
    (
        "永远画图例（单条曲线时也占一行）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('    if len(series) > 1:\n        option["legend"] = {',
          '    if True:\n        option["legend"] = {')],
        ["单条曲线时不画图例"],
    ),
    (
        "表格绕开概要页缓存再拉一次全量（页面加载打两次全量请求）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('            executions: list[dict[str, Any]] = _cached_all_executions()',
          '            executions: list[dict[str, Any]] = AppContext().user_strategy_api.list_executions()')],
        ["表格复用曲线已拉取的全量执行记录"],
    ),
    (
        "全量执行记录缓存不再命中（曲线与表格各拉一次）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('    if _exec_state["data"] and now - float(_exec_state["at"]) < _EXEC_TTL_SECONDS:',
          '    if False:')],
        ["全量执行记录只拉一次"],
    ),
    (
        "表格退回用执行记录的 current_return_rate（已实现口径）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('                rate_pct: float | None = current_return_rate_pct(\n'
          '                    rec, unrealized_profit(positions, price_map),\n'
          '                )',
          '                rate_pct: float | None = float(rec.get("current_return_rate", 0) or 0) * 100.0')],
        ["us-1 已实现 3,000 + 未实现 200"],
    ),
    (
        "表格拿持仓的 profit_amount（成交时点快照）当浮盈",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('                    rec, unrealized_profit(positions, price_map),',
          '                    rec, sum(float(p.get("profit_amount", 0) or 0) for p in positions),')],
        ["us-1 已实现 3,000"],
    ),
    (
        "行情缓存不再命中（每次渲染都打接口）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('    if _price_state["key"] == key and now - float(_price_state["at"]) < _PRICE_TTL_SECONDS:',
          '    if False:')],
        ["重复渲染命中 TTL 缓存"],
    ),
    (
        "曲线只按自己窗口内记录的持仓取价（与表格错开，各打一次接口）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('                [list(rec.get("positions", []) or [])\n'
          '                 for rec in latest_execution_map(all_execs).values()]',
          '                [list(ex.get("positions", []) or []) for ex in window_execs]')],
        ["表格与曲线共用同一份行情缓存"],
    ),
    # ---- 近期运行状态：策略名必须取用户策略名称（2026-09-23） ----
    (
        "近期运行状态的策略名按模板 ID 取（同模板多实例显示同一个名字）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('                        us_names.get(str(s.get("user_strategy_id", "") or ""), "")\n'
          '                        or tpl_names.get(str(s.get("strategy_id", "")), "")',
          '                        tpl_names.get(str(s.get("strategy_id", "")), "")')],
        ["us-2 → 策略二"],
    ),
    (
        "近期运行状态不回落模板名（会话缓存没有该策略时只剩 --）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('                        or tpl_names.get(str(s.get("strategy_id", "")), "")\n'
          '                        or "--"',
          '                        or "--"')],
        ["回退模板名"],
    ),
    # ---- 实时信号日志表：用户策略名 / 无盈亏额 / 代码下有名称（2026-09-23） ----
    (
        "信号表策略列按模板 ID 取（同模板多实例串名）",
        MONITOR_PAGE, "SMOKE_MONITOR_PAGE_SRC",
        [('            us_names[us_id] = str(\n'
          '                us.get("name") or tpl_names.get(sid, "") or sid or ""\n'
          '            )',
          '            us_names[us_id] = str(tpl_names.get(sid, "") or "")')],
        ["同模板的另一个用户策略显示自己的名称"],
    ),
    (
        "信号表把「盈亏额」列加回来",
        MONITOR_PAGE, "SMOKE_MONITOR_PAGE_SRC",
        [('                            ("盈亏率", 1), ("原因", 2),',
          '                            ("盈亏率", 1), ("盈亏额", 1), ("原因", 2),')],
        ["信号表 7 列、已去掉「盈亏额」列"],
    ),
    (
        "股票代码下方不显示股票名称（只有代码一行）",
        MONITOR_PAGE, "SMOKE_MONITOR_PAGE_SRC",
        [('                                    ui.label(stock_name or "--").classes("text-xs").style("color: var(--qs-faint);")',
          '                                    ui.label("--").classes("text-xs").style("color: var(--qs-faint);")')],
        ["股票代码单元格：代码在上、名称在下"],
    ),
    (
        "股票名称缓存不再命中（每次渲染都请求股票信息接口）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('        if code in _NAME_CACHE:\n            continue',
          '        if not _NAME_CACHE:\n            continue')],
        ["股票名称走进程级缓存"],
    ),
    # ---- 信号表恒空的两个回归点（2026-09-23）----
    # 现象：改代码触发 NiceGUI 热重载后，监控页信号表一条都不显示、刷新也不恢复。
    # 原因：内存信号容器被清空，而「已加载历史信号」标志存在跨进程保留的会话里，
    # 布尔标志仍为真 ⇒ load_history 直接返回，历史信号再也不加载。
    (
        "历史信号标志退回布尔值（热重载后内存已空、会话标志仍为真，信号表恒空）",
        SIGNAL_MGR, "SMOKE_SIGNAL_MGR_SRC",
        [('            if app.storage.user.get(_HISTORY_FLAG_KEY, "") == _PROCESS_TOKEN:\n'
          '                return',
          '            if app.storage.user.get(_HISTORY_FLAG_KEY, False):\n'
          '                return')],
        ["标志位按**进程 token** 比对"],
    ),
    (
        "加载失败后不回滚历史信号标志（本会话在本进程内不再重试）",
        SIGNAL_MGR, "SMOKE_SIGNAL_MGR_SRC",
        [('                if app.storage.user.get(_HISTORY_FLAG_KEY, "") == _PROCESS_TOKEN:\n'
          '                    del app.storage.user[_HISTORY_FLAG_KEY]',
          '                pass')],
        ["加载失败时回滚标志"],
    ),
    (
        "子页面外壳不加载历史信号（直接访问/刷新监控页时信号表恒空）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        # 锚点必须带上后面的 _apply_theme_for_session()：main_page()（首页）里也有
        # 一行同样的 load_history，只匹配这一行会替换到首页那处、外壳仍然完好。
        [('    AppContext().signal_manager.load_history(limit=10)\n\n    _apply_theme_for_session()',
          '    pass\n\n    _apply_theme_for_session()')],
        ["子页面外壳（main_page_with_content）加载历史信号"],
    ),
    (
        "子页面外壳不准备会话策略缓存（按 ID 过滤取不到任何信号）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('    _ensure_user_strategies()\n\n    # 所有页面入口统一把「最近的历史信号」补进内存',
          '    pass\n\n    # 所有页面入口统一把「最近的历史信号」补进内存')],
        ["子页面外壳（main_page_with_content）准备会话策略缓存"],
    ),
    # ---- 信号重复（2026-09-23）：容器进程级 + 标志会话级 ⇒ 每个会话各追加一份 ----
    # 真机现象：监控页每条信号出现 3 次、统计卡从 10 条虚增到 30 条。
    (
        "信号入队不做去重（每个会话各追加一份同样的历史信号）",
        SIGNAL_MGR, "SMOKE_SIGNAL_MGR_SRC",
        [('            if any(signal_key(item) == key for item in us.signal):\n'
          '                return False',
          '            if False:\n'
          '                return False')],
        ["跨会话重复加载同一批历史信号：按去重键合并，不复制"],
    ),
    (
        "去重键改成只在缺时间戳时才用（历史带 id、MQ 无 id ⇒ 同一条信号两份）",
        SIGNAL_MGR, "SMOKE_SIGNAL_MGR_SRC",
        [('    created: str = str(sig.get("create_time", "") or "")\n'
          '    if created:',
          '    created: str = str(sig.get("create_time", "") or "")\n'
          '    if not created:')],
        ["MQ 实时信号与历史记录按同一去重键合并（不是按 id 去重）"],
    ),
    # ---- 平均口径的两条硬要求（2026-09-30）：窗口外策略别丢、当天没交易的别归零 ----
    (
        "聚合时窗口外策略没进起始基准（少一个分母，均值偏一边）",
        MAIN_PAGE, "SMOKE_MAIN_PAGE_SRC",
        [('    carry_in: dict[str, float] = (\n'
          '        strategy_levels_before(source, window_days[0]) if window_days else {}\n'
          '    )',
          '    carry_in: dict[str, float] = {}')],
        ["传 history ⇒ 起始基准补上窗口外 us-3 的 1.0（均值 (2+1)/2 = 1.5）"],
    ),
    (
        "daily_average_rates 忽略 carry_in（窗口外停交易的策略不进均值）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('    levels: dict[str, float] = dict(carry_in or {})',
          '    levels: dict[str, float] = {}')],
        ["daily_average_rates 带 carry_in ⇒ 窗口外停交易的策略仍计入平均"],
    ),
    (
        "当天没交易的策略直接归零（均值随当天谁交易而跳）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('        levels.update(per_day[day])\n'
          '        rates: list[float] = effective_rates(levels.values())\n'
          '        result[day] = round(sum(rates) / len(rates), 2) if rates else 0.0',
          '        levels = dict(per_day[day])\n'
          '        rates: list[float] = effective_rates(levels.values())\n'
          '        result[day] = round(sum(rates) / len(rates), 2) if rates else 0.0')],
        ["daily_average_rates = 各策略取值的**平均值**，当天没交易的策略沿用最近一次取值"],
    ),
    (
        "曲线末点改回求和（旧口径，与需求方选定的平均值口径不符）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('    return round(sum(rates) / len(rates), 2)',
          '    return round(sum(rates), 2)')],
        ["average_current_rate_pct = 各策略含浮盈收益率的**平均值**"],
    ),
    (
        "零收益策略算进平均（新建策略把平均值往 0 拉）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('    return [v for v in levels if abs(v) > _ZERO_EPS]',
          '    return list(levels)')],
        ["daily_average_rates 收益为 0 的策略不计入平均（7/2 = 3.5，不是 7/3 = 2.33）",
         "average_current_rate_pct 排除收益为 0 的策略（(3.2+5.0+5.0+0)/4 是错的）"],
    ),
    (
        "每天的分母写成全部策略数（零收益策略仍占分母）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('        rates: list[float] = effective_rates(levels.values())\n'
          '        result[day] = round(sum(rates) / len(rates), 2) if rates else 0.0',
          '        rates: list[float] = effective_rates(levels.values())\n'
          '        result[day] = round(sum(rates) / max(len(levels), 1), 2)')],
        ["daily_average_rates 收益为 0 的策略不计入平均（7/2 = 3.5，不是 7/3 = 2.33）"],
    ),
    (
        "单策略曲线在它没交易的日期断线（不沿用最近一次取值）",
        HOLDINGS, "SMOKE_HOLDINGS_SRC",
        [('        if bucket:\n'
          '            level = sum(bucket.values())\n'
          '        out.append(',
          '        if bucket:\n'
          '            level = sum(bucket.values())\n'
          '        else:\n'
          '            level = None\n'
          '        out.append(')],
        ["对比曲线在它没交易的日期沿用最近一次取值（不是断线、更不是 0）"],
    ),
    # ---- 个人喜好页：推送星级滑块（2026-09-30 新增）----
    (
        "保存载荷漏掉星级字段（PUT 全量替换 ⇒ 星级被静默清成 0）",
        PREFERENCES_PAGE, "SMOKE_PREFERENCES_SRC",
        [('            "push_buy_min_star": state["push_buy_min_star"],\n'
          '            "push_sell_min_star": state["push_sell_min_star"],\n',
          '')],
        ["保存载荷带 push_buy_min_star（PUT 全量替换，漏了就被清成 0）"],
    ),
    (
        "星级上限写成 10（与服务端 0~5 不一致，保存会被 400 拒）",
        PREFERENCES_PAGE, "SMOKE_PREFERENCES_SRC",
        [('                                    max=_MAX_PUSH_STAR,',
          '                                    max=10.0,')],
        ["滑块 max = 5.0（与服务端 MAX_PUSH_STAR 一致）"],
    ),
    (
        "步长改成 1.0（0.5 星的档位没了）",
        PREFERENCES_PAGE, "SMOKE_PREFERENCES_SRC",
        [('                                    step=_STAR_STEP,',
          '                                    step=1.0,')],
        ["滑块 step = 0.5"],
    ),
    (
        "星级区只看微信开关（只开短信时不显示）",
        PREFERENCES_PAGE, "SMOKE_PREFERENCES_SRC",
        [('                bool(state["enable_wx_push"] or state["enable_phone_text"])',
          '                bool(state["enable_wx_push"])')],
        ["只开短信推送 ⇒ 星级区同样显示"],
    ),
    (
        "拖动滑块不写回 state（页面能拖，保存还是旧值）",
        PREFERENCES_PAGE, "SMOKE_PREFERENCES_SRC",
        [('            state[field] = star', '            star = star')],
        ["保存载荷带 push_buy_min_star（PUT 全量替换，漏了就被清成 0）"],
    ),
    (
        "_star_val 不夹上界（脏数据 9 会被服务端 400 拒）",
        PREFERENCES_PAGE, "SMOKE_PREFERENCES_SRC",
        [('    if star > _MAX_PUSH_STAR:\n'
          '        return _MAX_PUSH_STAR\n',
          '')],
        ["_star_val 超过 5 夹到 5.0（否则服务端 400）"],
    ),
    (
        "切主题时不补星级字段（服务端旧记录没有 ⇒ 星级被清零）",
        PREFERENCES_PAGE, "SMOKE_PREFERENCES_SRC",
        [('        for key in ("push_buy_min_star", "push_sell_min_star"):\n'
          '            if payload.get(key) is None:\n'
          '                payload[key] = state[key]\n',
          '')],
        ["切主题时补上页面当前星级（服务端记录里没有也不清零）"],
    ),
    # ---- 登录页：HTTP 4xx 必须透出后端 detail（2026-10-01）----
    (
        "注册 409 没接住（落到 except Exception ⇒ 弹「网络错误: Client error '409 Conflict'」）",
        LOGIN_PAGE, "SMOKE_LOGIN_PAGE_SRC",
        [('        except httpx.HTTPStatusError as e:\n'
          '            # 注册的 409 是**正常业务分支**（账号已存在 / 邮箱已被注册 /\n'
          '            # 手机号已被注册），后端的 detail 就是该给用户看的那句话。\n'
          '            detail: str = extract_error_detail(e.response)\n'
          '            ui.notify(\n'
          '                f"注册失败: {detail}" if detail\n'
          '                else f"注册失败 (HTTP {e.response.status_code})，请稍后重试",\n'
          '                type="negative",\n'
          '            )\n',
          '')],
        ["注册 409 ⇒ 提示「注册失败: 账号已存在」"],
    ),
    (
        "提示里塞的是 httpx 的英文异常串（用户读到 Client error '409 Conflict'）",
        LOGIN_PAGE, "SMOKE_LOGIN_PAGE_SRC",
        [('            # 手机号已被注册），后端的 detail 就是该给用户看的那句话。\n'
          '            detail: str = extract_error_detail(e.response)',
          '            # 手机号已被注册），后端的 detail 就是该给用户看的那句话。\n'
          '            detail: str = str(e)')],
        ["不再把 httpx 的英文异常串暴露给用户"],
    ),
    (
        "错误详情只认 detail、不认 message（统一 ApiResponse 的提示取不到）",
        API_CLIENT, "SMOKE_API_CLIENT_SRC",
        [('    for key in ("detail", "message"):', '    for key in ("detail",):')],
        ["统一 ApiResponse 的 message 也能取（detail 缺失时回退）"],
    ),
    (
        "422 的字段级错误列表被拼成句子（弹出一串 {\"loc\": [...]} 给用户看）",
        API_CLIENT, "SMOKE_API_CLIENT_SRC",
        [('        value: Any = body.get(key)\n'
          '        if isinstance(value, str) and value.strip():\n'
          '            return value.strip()',
          '        value: Any = body.get(key)\n'
          '        if value:\n'
          '            return str(value)')],
        ["422 的 detail 是错误列表 ⇒ 不硬拼成句子，返回空串走状态码兜底"],
    ),
]


def _targets_for(env_key: str) -> list[pathlib.Path]:
    """该环境变量会把哪个检查脚本指向改坏副本。

    ``SMOKE_STRATEGIES_SRC`` 同时被两个脚本读取（回测检查用它做「入口是否
    带了 source」的文本断言，对话框检查用它当真源码 exec），因此两个都要跑。
    ``SMOKE_MONITOR_PAGE_SRC`` 是监控检查专用的页源码开关
    （不能复用 SMOKE_PAGE_SRC：那个名字回测检查也在用、指向的是回测页）。
    ``SMOKE_MAIN_PAGE_SRC`` / ``SMOKE_HOLDINGS_SRC`` / ``SMOKE_API_SRC`` 是概要页
    检查的三个开关：分别换页面副本、``core/holdings.py``（口径算法的分支只能在
    它上面验）、``api/user_strategy.py``（接口路径与分页参数只做文本断言）。
    """
    if env_key == "SMOKE_STRATEGIES_SRC":
        return [BACKTEST_CHECK, DIALOG_CHECK]
    if env_key == "SMOKE_MONITOR_PAGE_SRC":
        return [MONITOR_CHECK]
    if env_key == "SMOKE_HOLDINGS_SRC":
        # 口径模块被概要页与监控页共用：两边的断言都守同一段算法，
        # 改坏后要两个检查都跑，任一条断言报错即算抓到。
        return [MAIN_PAGE_CHECK, MONITOR_CHECK]
    if env_key == "SMOKE_MAIN_PAGE_SRC":
        return [MAIN_PAGE_CHECK]
    if env_key == "SMOKE_API_SRC":
        return [MAIN_PAGE_CHECK]
    if env_key == "SMOKE_SIGNAL_MGR_SRC":
        # 历史信号的「本进程本会话只加载一次」语义在 mq/signal_manager.py 里，
        # 由监控页检查断言（页面侧只调用它、不实现它）。
        return [MONITOR_CHECK]
    if env_key == "SMOKE_PREFERENCES_SRC":
        # 个人喜好页检查专用（推送星级滑块与保存载荷只在这一页上验）
        return [PREFERENCES_CHECK]
    if env_key == "SMOKE_LOGIN_PAGE_SRC":
        # 登录页检查专用（4xx 提示文案只在这一页上验）
        return [LOGIN_CHECK]
    if env_key == "SMOKE_API_CLIENT_SRC":
        # 错误详情提取在 api/client.py 里，登录页检查会 exec 改坏副本
        return [LOGIN_CHECK]
    return [BACKTEST_CHECK]


def _run_case(
    name: str,
    src: pathlib.Path,
    env_key: str,
    mutations: list[tuple[str, str]],
    expected: list[str],
) -> bool:
    """改坏一处源码 → 子进程跑对应检查 → 要求报错且命中预期断言。"""
    original: str = src.read_text(encoding="utf-8")
    broken: str = original
    for old, new in mutations:
        if old not in broken:
            print(f"[驱动错误] 用例「{name}」的锚点未命中: {old[:60]!r}")
            return False
        broken = broken.replace(old, new, 1)
    if broken == original:
        print(f"[驱动错误] 用例「{name}」没有真正改动源码")
        return False

    broken_path: pathlib.Path = TMP / f"{name[:24].replace('/', '_')}_{src.name}"
    broken_path.write_text(broken, encoding="utf-8")

    env: dict[str, str] = dict(os.environ)
    env[env_key] = str(broken_path)

    hit: bool = False
    detail: list[str] = []
    for target in _targets_for(env_key):
        result: subprocess.CompletedProcess[str] = subprocess.run(
            [PYTHON, str(target)], cwd=str(ROOT), env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        out: str = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0 and all(k in out for k in expected):
            hit = True
            break
        detail.append(f"{target.name}: exit={result.returncode}")

    mark: str = "抓到" if hit else "漏网"
    print(f"[{mark}] {name}")
    if not hit:
        print(f"          期望关键词={expected}；各检查结果 {detail}")
        for line in out.splitlines():
            if "FAIL" in line or "RESULT" in line:
                print(f"          {line}")
    return hit


def main() -> int:
    # 前置：被改坏的文件路径必须存在（否则是驱动写错了，不是断言的问题）
    for src in {c[1] for c in CASES}:
        if not src.is_file():
            print(f"[驱动错误] 源文件不存在: {src}")
            return 2

    # 监控页断言的锚点检查：策略监控页那条用例读的是回测检查的接线一节
    hit_count: int = 0
    for case in CASES:
        if _run_case(*case):
            hit_count += 1
    print()
    print("=" * 72)
    print(f"反向验证：{hit_count}/{len(CASES)} 个改坏用例被抓到")
    if hit_count != len(CASES):
        print("有漏网用例 ⇒ 对应断言没有信息量，请补强断言")
    print("=" * 72)
    return 0 if hit_count == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
