"""把策略参数同步到服务端（策略模板 default_params 与用户策略 strategy_params）。

为什么需要这个脚本
------------------
策略参数有三个来源，必须保持一致：

  ① 策略类属性（`SwingTradingStrategy.SR_WINDOW` 等，源码默认值）
  ② `cfg/strategy.yaml` 对应配置节 —— **回测生效**
  ③ 服务端：策略模板 `default_params` 与用户策略 `strategy_params` —— **实盘生效**

三者分叉时，同一份代码在实盘与回测会跑出不同的参数，而两边日志都「看起来正常」。
①②由 `tests/strategy/test_param_defaults_consistency.py` 守住；③ 在服务端，本端测试够不着，
所以用这个脚本对齐。

实盘侧还有一层容易被忽略的语义：`BaseStrategy._apply_resolved_params` 是**择一**，
不是逐字段合并 ——

    user_strategy.strategy_params（非空则独占） > strategy.default_params > cfg/strategy.yaml

也就是说，只要用户策略配了 `strategy_params`，模板的 `default_params` 对它**完全不生效**。
漏字段时该字段不会去模板取，而是回退到**策略类属性**。因此模板与用户策略两处都要同步，
只同步模板时，带 `strategy_params` 的用户策略在界面上仍看不到、也调不了新参数。

设计取舍
--------
- **默认「补缺」，不「覆盖」（2026-09-29 修订）**：服务端已有的字段一律保留原值，
  只把服务端缺失的字段用本端值补上。原因是两类载体的语义不同：
  模板 `default_params` 是「新用户策略继承的默认值」，用户策略 `strategy_params`
  是「用户自己的定制值」。界面上把 ADX 门槛从 25 调到 20 是刻意的选择、不是脏数据，
  若被脚本冲回代码默认值，等于界面调参无法持久化。要主动把服务端拉向本端值，
  必须显式加 `--align-values`。
- **值取自 `cfg/strategy.yaml`，不硬编码**：硬编码的值会在下次改参数时第二次分叉。
  yaml 值与类属性默认值的相等由 `tests/strategy/test_param_defaults_consistency.py`
  守住，因此 yaml 值即「代码默认值」。`desc` 只用于服务端界面展示，与策略行为无关。
- **写入时传合并后的全量字段**：服务端 `PATCH` 对 `default_params` / `strategy_params`
  是整体赋值还是深合并，OpenAPI 里没有承诺（schema 是 `additionalProperties: true`
  的自由对象）。因此不能只传缺失字段（若服务端是赋值语义，已有字段会被清掉），
  一律传「服务端现值 + 本次补入字段」的合并结果，两种语义下结果都正确。
- **本端未定义的服务端字段一律保留**：可能是已废弃参数或本端尚未认识的参数。
  脚本只维护本端定义过的字段，不认识的字段不动，仅打印提示。
- **用户策略的 `strategy_params` 为空（None/{}）时跳过**：空值表示「跟随模板」，
  写入会破坏这一继承关系。
- **默认 dry-run**：不带 `--yes` 只打印差异，不写任何数据。

用法
----
`--strategy` 支持 `swing`（波段）/ `strong`（强趋势）/ `volume`（成交量放大），
三者与 `backtest/main.py --strategy` 的快捷名一致。

    # 查看差异（不写入）
    python sync_strategy_template_params.py --strategy swing

    # 补缺（默认语义）：只补服务端缺失的字段，保留界面上调过的值
    python sync_strategy_template_params.py --strategy swing --yes

    # 只补策略模板 / 只补用户策略
    python sync_strategy_template_params.py --strategy swing --scope template --yes
    python sync_strategy_template_params.py --strategy swing --scope user --yes

    # 全量拉齐：用本端值覆盖服务端已有值（会冲掉界面调参，需显式开启）
    python sync_strategy_template_params.py --strategy swing --align-values --yes

需要 `cfg/stock.cfg [server] admin_token`（写接口要求管理员身份）。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import yaml

from api.client import ApiClient, ApiConfig
from api.strategy import StrategyApi
from api.user_strategy import UserStrategyApi
from utils.tools import load_admin_token, resource_path

# ---- 策略注册表 ----
# key 为 CLI 快捷名（与 backtest CLI 的快捷名保持一致）；
# yaml_section 对应 cfg/strategy.yaml 的配置节；template_id 是服务端策略模板 ID。
# 新增策略时：先在本表登记，再到 STRATEGY_FIELDS 补字段清单，
# 并把源码路径加进 tests/test_sync_strategy_template_params.py::STRATEGY_SOURCES。
STRATEGY_META: dict[str, dict[str, str]] = {
    "swing": {
        "label": "波段交易策略",
        "yaml_section": "swing_trading_strategy",
        "template_id": "6a662b9b28893aa08cf31092",
    },
    "strong": {
        "label": "强趋势策略",
        "yaml_section": "strong_rebound_strategy",
        "template_id": "6a5884d528893aa08c3e805b",
    },
    "volume": {
        "label": "成交量放大策略",
        "yaml_section": "volume_expansion_strategy",
        "template_id": "6abb3227276f35f51ff8381c",
    },
}

# ---- 字段清单：(节, 字段名, desc) ----
# 字段名必须与策略 `apply_params` 中读取的 key 逐字一致，否则同步了也不生效。
# 顺序即写入顺序，按「选股 → 买入 → 卖出 → 风控」排列，便于与服务端界面对照。
# `desc` 取 cfg/strategy.yaml 里各字段的注释，供服务端界面展示。
STRATEGY_FIELDS: dict[str, list[tuple[str, str, str]]] = {
    "swing": [
        ("match", "sr_window", "极值滑动窗口"),
        ("match", "sr_tolerance", "极值误差"),
        ("match", "trend_days_limit", "趋势判定窗口（ADX 周期）"),
        (
            "match",
            "trend_adx_threshold",
            "ADX 强度门槛（低于此值视为震荡，不做趋势过滤）",
        ),
        ("match", "trend_kline_days", "趋势判定日K根数"),
        ("buy", "buy_sr_window", "买入滑动窗口"),
        ("sell", "sell_sr_window", "卖出滑动窗口"),
        ("sell", "drawdown_pct", "持仓回撤百分率"),
        (
            "sell",
            "drawdown_score",
            "持仓回撤达阈值时计入卖出判定的分值（10 分制，负值）",
        ),
        (
            "risk",
            "max_drawdown_pct",
            "账户级回撤阈值（%）：账户已实现权益自峰值回撤达此值时，"
            "在卖出判定中计入 drawdown_score 分值（不冻结开新仓）；0 或正数表示关闭",
        ),
        (
            "risk",
            "drawdown_score",
            "账户回撤达阈值时计入卖出判定的分值（10 分制，负值）",
        ),
    ],
    "strong": [
        ("match", "pe_limit", "市盈率上限（TTM），超过则不选"),
        ("match", "profit_limit", "利润增长率下限（%）"),
        ("match", "trend_days_limit", "趋势判定周期：7~15 对应短线到中长线"),
        ("match", "consecutive_days", "反弹/回撤因子的时间窗口（连续交易日数）"),
        (
            "match",
            "up_trend_score",
            "向上趋势分数阈值：趋势因子得分 >= 此值时认为趋势行情",
        ),
        (
            "match",
            "rebound_score",
            "连续反弹分值：趋势不达标时需满足此分值才买入",
        ),
        ("buy", "consecutive_minutes", "成交量放大因子的时间窗口（连续分钟数）"),
        ("buy", "trend_minutes_limit", "日内分钟趋势限制"),
        ("buy", "min_score", "买入最小分数：当天累计加分需 >= 此值才触发买入"),
        ("sell", "trend_minutes_limit", "日内分钟趋势限制"),
        ("sell", "vwap_minutes_limit", "VWAP 均价连续分钟"),
        (
            "sell",
            "drawdown_pct",
            "持仓回撤阈值（%）：自持仓最高点回撤超过此值触发卖出",
        ),
        ("sell", "max_score", "卖出最大分数：当天累计减分 <= 此值触发卖出"),
    ],
    "volume": [
        ("match", "volume_days", "成交量因子窗口（因子内部下限 5）"),
        (
            "match",
            "volume_mode",
            "成交量方向：reversion=缩量看多（当前默认）/ momentum=放量看多",
        ),
        (
            "match",
            "quiet_volume_score",
            "reversion 模式下的缩量上限：成交量打分 <= 此值才允许买入",
        ),
        (
            "match",
            "partner_factor",
            "搭配因子：boll_sr / kline_sr / multi_trend / ma / vwap / adx",
        ),
        (
            "match",
            "partner_min_score",
            "搭配因子门槛（>=）：0 表示不在阻力区、未破支撑",
        ),
        ("match", "volume_score", "选股成交量打分门槛（10 分制）"),
        ("match", "min_score", "选股综合分门槛（10 分制）"),
        (
            "buy",
            "min_minutes",
            "分钟数据最少条数（实际门槛取与本策略因子窗口的较大值）",
        ),
        ("buy", "volume_score", "分钟级成交量打分门槛（momentum 模式）"),
        ("buy", "partner_factor", "搭配因子，空 = 跟随 match.partner_factor"),
        ("buy", "volume_mode", "成交量方向，空 = 跟随 match.volume_mode"),
        ("buy", "quiet_volume_score", "缩量上限（reversion 模式）"),
        ("buy", "partner_min_score", "搭配因子门槛（>=）"),
        ("buy", "min_score", "买入综合分门槛（10 分制）"),
        ("buy", "volume_weight", "成交量侧权重（成交量为主、搭配做确认）"),
        ("buy", "partner_weight", "搭配侧权重"),
        ("sell", "min_minutes", "分钟数据最少条数（实际门槛取与因子窗口的较大值）"),
        ("sell", "max_score", "卖出分阈值：累计负分 <= 此值触发卖出"),
        (
            "sell",
            "drawdown_pct",
            "持仓回撤阈值（%）：自持仓最高点回撤超过此值时计入卖出分值",
        ),
        ("sell", "vwap_minutes", "均价（VWAP）窗口"),
        (
            "risk",
            "max_drawdown_pct",
            "账户级回撤阈值（%）：账户已实现权益自峰值回撤达此值时，"
            "在卖出判定中计入 drawdown_score 分值（不冻结开新仓）；0 或正数表示关闭",
        ),
        (
            "risk",
            "drawdown_score",
            "账户回撤达阈值时计入卖出判定的分值（10 分制，负值）",
        ),
    ],
}


def _load_yaml_section(section: str) -> dict[str, Any]:
    """读取 cfg/strategy.yaml 中指定配置节的参数。

    :param section: 配置节名，如 swing_trading_strategy
    :return: 该节的参数字典（纯值结构），读取失败时抛错
    """
    path: str = resource_path("cfg/strategy.yaml")
    with open(path, "r", encoding="utf-8") as f:
        config: Any = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise ValueError(f"cfg/strategy.yaml 顶层结构异常: {type(config).__name__}")
    node: Any = config.get(section)
    if not isinstance(node, dict):
        raise ValueError(f"cfg/strategy.yaml 缺少配置节 [{section}]")
    return node


def _build_plain(strategy: str) -> dict[str, dict[str, Any]]:
    """从 yaml 按字段清单组装**纯值**参数（lib 格式，与 user_strategy 一致）。

    :param strategy: CLI 快捷名
    :return: 纯值参数，如 {"match": {"sr_window": 10, ...}, ...}
    """
    section: str = STRATEGY_META[strategy]["yaml_section"]
    yaml_node: dict[str, Any] = _load_yaml_section(section)

    plain: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for group, field, _desc in STRATEGY_FIELDS[strategy]:
        group_node: Any = yaml_node.get(group)
        if not isinstance(group_node, dict) or field not in group_node:
            missing.append(f"{group}.{field}")
            continue
        plain.setdefault(group, {})[field] = group_node[field]

    if missing:
        raise ValueError(
            f"cfg/strategy.yaml[{section}] 缺少字段: {', '.join(missing)}。"
            "字段清单与配置节已分叉，先核对 apply_params 读取的 key。"
        )
    return plain


def _wrap_for_template(
    strategy: str, plain: dict[str, dict[str, Any]]
) -> dict[str, dict[str, dict[str, Any]]]:
    """把纯值参数包成策略模板使用的 `{desc, value}` 格式。

    :param strategy: CLI 快捷名（用于取 desc）
    :param plain: 纯值参数
    :return: 包装后的 default_params
    """
    descs: dict[str, str] = {
        f"{g}.{f}": d for g, f, d in STRATEGY_FIELDS[strategy]
    }
    wrapped: dict[str, dict[str, dict[str, Any]]] = {}
    for group, fields in plain.items():
        for field, val in fields.items():
            wrapped.setdefault(group, {})[field] = {
                "desc": descs.get(f"{group}.{field}", ""),
                "value": val,
            }
    return wrapped


def _flatten(params: Any) -> dict[str, Any]:
    """把（可能是 `{desc, value}` 包装的）参数表压平成 `节.字段 -> 值`。"""
    flat: dict[str, Any] = {}
    if not isinstance(params, dict):
        return flat
    for group, node in params.items():
        if not isinstance(node, dict):
            continue
        for field, val in node.items():
            if isinstance(val, dict) and "value" in val:
                flat[f"{group}.{field}"] = val.get("value")
            else:
                flat[f"{group}.{field}"] = val
    return flat


def _merge_params(
    current: Any, target: dict[str, dict[str, Any]], overwrite: bool
) -> tuple[dict[str, Any], list[str]]:
    """把本端目标参数合并进服务端现值，返回 (合并结果, 本次写入的字段列表)。

    合并粒度是 `节.字段`。

    - `overwrite=False`（补缺，默认）：同名键**保留服务端现值**，只补 target 独有的键。
      界面上调过的参数因此不会被冲掉，脚本可反复跑且幂等。
    - `overwrite=True`（拉齐，`--align-values`）：同名键用 target 值覆盖。
    两种模式都**保留服务端独有的键**（本端未定义的字段），脚本不动不认识的参数。

    返回值必须全量写回服务端，不能只写差值：服务端 `PATCH` 对嵌套对象是赋值还是
    深合并没有承诺，只传部分字段时若为赋值语义会把已有字段清掉。

    :param current: 服务端当前参数（纯值或 `{desc, value}` 包装均可）
    :param target: 本端目标参数，结构与 current 对应
    :param overwrite: True 用本端值覆盖同名键；False 只补缺
    :return: (合并后的全量参数, 本次写入的 `节.字段` 列表)
    """
    merged: dict[str, Any] = {}
    if isinstance(current, dict):
        for group, node in current.items():
            # 复制一层，避免直接改动调用方持有的服务端响应对象
            merged[group] = dict(node) if isinstance(node, dict) else node

    written: list[str] = []
    for group, fields in target.items():
        if not isinstance(fields, dict):
            continue
        bucket: Any = merged.get(group)
        if not isinstance(bucket, dict):
            bucket = {}
            merged[group] = bucket
        for field, val in fields.items():
            if field in bucket and not overwrite:
                continue  # 服务端已有值，保留（含界面上的调参）
            bucket[field] = val
            written.append(f"{group}.{field}")
    return merged, written


def _print_diff(title: str, current: Any, target: Any) -> bool:
    """打印当前值与目标值的差异。

    :return: 是否存在差异
    """
    cur_flat: dict[str, Any] = _flatten(current)
    tgt_flat: dict[str, Any] = _flatten(target)

    print(f"--- {title} ---")
    print(f"{'字段':<30} {'服务端当前':<16} {'本端目标':<16} 状态")
    print("-" * 84)
    changed: bool = False

    for key in sorted(set(cur_flat) | set(tgt_flat)):
        c: Any = cur_flat.get(key, "<缺失>")
        t: Any = tgt_flat.get(key, "<本端未定义>")
        if key not in cur_flat:
            status, changed = "新增", True
        elif key not in tgt_flat:
            status, changed = "仅服务端有", True
        elif c != t:
            status, changed = "值不同", True
        else:
            status = "一致"
        print(f"{key:<30} {str(c):<16} {str(t):<16} {status}")
    print()
    return changed


def _print_extra(title: str, current: Any, target: dict[str, Any]) -> None:
    """提示服务端有、本端未定义的字段（脚本不会改动它们）。

    保留而不是删除：这类字段可能是已废弃参数，也可能是本端尚未纳入管理的参数，
    脚本的职责只是维护本端定义过的字段，判断不了的就留在原地。

    :param title: 打印用的标题
    :param current: 服务端当前参数
    :param target: 本端目标参数
    """
    extra: list[str] = sorted(set(_flatten(current)) - set(_flatten(target)))
    if extra:
        print(f"提示：{title} 另有 {len(extra)} 个本端未定义的字段，已原样保留: {extra}")


def _verify(title: str, after: Any, target: Any, written: list[str]) -> bool:
    """回读校验。

    校验两件事：

    1. 本端定义的字段在服务端**齐全**（补缺的目的就是让这些字段可见、可调）；
    2. 本次写入的字段，回读值确实等于本端目标值。

    **不**校验「所有字段都等于本端值」：补缺语义下服务端保留了界面上的调参，
    值不同是预期结果，不是失败。拉齐模式（`written` 为全量）下两者等价。

    :param title: 打印用的标题
    :param after: 写入后的服务端参数
    :param target: 本端目标参数
    :param written: 本次写入的 `节.字段` 列表
    :return: 是否通过
    """
    after_flat: dict[str, Any] = _flatten(after)
    tgt_flat: dict[str, Any] = _flatten(target)
    absent: list[str] = [k for k in tgt_flat if k not in after_flat]
    wrong: list[str] = [
        k for k in written if k in tgt_flat and after_flat.get(k, "<缺失>") != tgt_flat[k]
    ]
    if absent or wrong:
        if absent:
            print(f"[{title}] 回读校验失败，字段仍缺失: {absent}")
        if wrong:
            print(f"[{title}] 回读校验失败，写入值与目标不符: {wrong}")
        print(json.dumps(after, ensure_ascii=False, indent=2))
        return False
    print(
        f"[{title}] 回读校验通过：本端 {len(tgt_flat)} 个字段齐全，"
        f"本次写入 {len(written)} 个"
    )
    return True


def _sync_template(
    api: StrategyApi,
    template_id: str,
    wrapped: dict[str, Any],
    write: bool,
    align: bool,
) -> bool:
    """同步策略模板 default_params。

    :param align: False（默认）只补服务端缺失的字段；True 用本端值覆盖同名键
    :return: 同步后是否通过
    """
    tpl: dict[str, Any] = api.get_by_id(template_id)
    if not tpl:
        print(f"模板 {template_id} 读取失败或不存在")
        return False
    print(f"模板：{tpl.get('name')!r}  类名：{tpl.get('class_name')!r}  ID：{template_id}")

    current: Any = tpl.get("default_params")
    _print_diff("策略模板 default_params", current, wrapped)
    _print_extra("策略模板", current, wrapped)

    merged: dict[str, Any]
    written: list[str]
    merged, written = _merge_params(current, wrapped, overwrite=align)

    if not written:
        suffix: str = "" if align else "（已有值按补缺语义保留）"
        print(f"策略模板无待写入字段，未写入{suffix}\n")
        return True
    print(f"待写入字段（{len(written)} 个）: {sorted(written)}")
    if not write:
        print("[dry-run] 策略模板未写入\n")
        return True

    result: str = api.update(template_id, default_params=merged)
    print(f"策略模板 update 返回: {result}")
    after: dict[str, Any] = api.get_by_id(template_id)
    ok: bool = _verify("策略模板", after.get("default_params"), wrapped, written)
    print()
    return ok


def _sync_user_strategies(
    us_api: UserStrategyApi,
    template_id: str,
    label: str,
    plain: dict[str, Any],
    write: bool,
    align: bool,
) -> bool:
    """同步挂在该模板下的用户策略 strategy_params。

    跳过 `strategy_params` 为空的用户策略：空值表示「跟随模板」，写入会破坏继承关系。

    :param align: False（默认）只补服务端缺失的字段；True 用本端值覆盖同名键
    :return: 是否全部同步成功
    """
    rows: list[dict[str, Any]] = us_api.list_all()
    targets: list[dict[str, Any]] = [
        r
        for r in rows
        if isinstance(r, dict)
        and (r.get("strategy_id") or r.get("template_id")) == template_id
    ]
    if not targets:
        print(f"没有用户策略挂在模板 {template_id} 下，跳过\n")
        return True

    all_ok: bool = True
    for r in targets:
        uid: str = str(r.get("_id") or r.get("id") or "")
        name: str = str(r.get("name") or r.get("strategy_name") or uid)
        sp: Any = r.get("strategy_params")

        print(f"--- 用户策略 {name!r} (id={uid}) ---")
        if not sp:
            print("strategy_params 为空 ⇒ 跟随模板，跳过（不写入以保留继承关系）\n")
            continue

        _print_diff("用户策略 strategy_params", sp, plain)
        _print_extra(f"用户策略 {name!r}", sp, plain)

        merged: dict[str, Any]
        written: list[str]
        merged, written = _merge_params(sp, plain, overwrite=align)

        if not written:
            suffix: str = "" if align else "（已有值按补缺语义保留）"
            print(f"用户策略 {name!r} 无待写入字段，未写入{suffix}\n")
            continue
        print(f"待写入字段（{len(written)} 个）: {sorted(written)}")
        if not write:
            print(f"[dry-run] 用户策略 {name!r} 未写入\n")
            continue

        result: str = us_api.update(uid, strategy_params=merged)
        print(f"用户策略 update 返回: {result}")
        after_rows: list[dict[str, Any]] = us_api.list_all()
        after: Any = next(
            (
                a.get("strategy_params")
                for a in after_rows
                if isinstance(a, dict) and str(a.get("_id") or a.get("id")) == uid
            ),
            None,
        )
        ok: bool = _verify(f"用户策略 {name!r}", after, plain, written)
        all_ok = all_ok and ok
        print()

    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(
        description="同步策略参数到服务端（值取自 cfg/strategy.yaml）"
    )
    parser.add_argument(
        "--strategy",
        required=True,
        choices=sorted(STRATEGY_META),
        help="策略快捷名（当前支持: %s）" % ", ".join(sorted(STRATEGY_META)),
    )
    parser.add_argument(
        "--scope",
        choices=("all", "template", "user"),
        default="all",
        help="同步范围：all=模板+用户策略（默认）、template=仅模板、user=仅用户策略",
    )
    parser.add_argument(
        "--yes", action="store_true", help="实际写入；不带此参数只打印差异"
    )
    parser.add_argument(
        "--align-values",
        action="store_true",
        help=(
            "全量拉齐：用本端值覆盖服务端已有字段（默认关闭）。"
            "默认只补服务端缺失的字段，界面上调过的参数不会被覆盖"
        ),
    )
    args = parser.parse_args()

    meta: dict[str, str] = STRATEGY_META[args.strategy]
    plain: dict[str, dict[str, Any]] = _build_plain(args.strategy)
    wrapped: dict[str, dict[str, dict[str, Any]]] = _wrap_for_template(args.strategy, plain)

    print(f"策略：{meta['label']}   模板 ID：{meta['template_id']}")
    print(f"参数节：cfg/strategy.yaml[{meta['yaml_section']}]")
    print(f"字段数：{sum(len(v) for v in plain.values())} 个   范围：{args.scope}")
    mode: str = "拉齐（覆盖已有值）" if args.align_values else "补缺（保留已有值）"
    print(f"模式：{'写入' if args.yes else 'dry-run（只读）'} / {mode}\n")

    client = ApiClient(ApiConfig())
    token: str = load_admin_token()
    if not token:
        print("未读取到 admin_token（cfg/stock.cfg [server] admin_token），无法写入")
        return 1
    client.set_token(token)

    try:
        ok: bool = True
        if args.scope in ("all", "template"):
            ok = (
                _sync_template(
                    StrategyApi(client),
                    meta["template_id"],
                    wrapped,
                    args.yes,
                    args.align_values,
                )
                and ok
            )
        if args.scope in ("all", "user"):
            ok = (
                _sync_user_strategies(
                    UserStrategyApi(client),
                    meta["template_id"],
                    meta["label"],
                    plain,
                    args.yes,
                    args.align_values,
                )
                and ok
            )

        if not args.yes:
            print("\n[dry-run] 未写入任何数据。确认无误后加 --yes 执行。")
        return 0 if ok else 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
