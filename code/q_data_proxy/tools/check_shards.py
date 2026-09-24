"""多实例分片体检。

多实例部署（每个实例一个账号、一个 api_token）能否凑够采样，取决于两件事：
1. 各实例的同步范围是否互不重叠——重叠的 code 会被两个实例各采一遍，
   实时表里出现重复记录，MQTT 也会重复推送；
2. 单实例的股票数是否在套餐额度允许的范围内——股票太多则一轮轮询长于分钟窗口，
   窗口内采不到 2 条记录，分钟线的 volume/amount 会算成 0。

本脚本按传入的多个配置文件分别算出各实例的实际同步范围（agent 股票池 + agent
热门行业，即 real_time_hq_sync_task 真正遍历的列表），输出单实例数量、两两重叠
code，并按套餐参数核算容量上限。

用法（项目根目录执行，需要能连上 MongoDB）：

    python tools/check_shards.py cfg/stock_1.cfg cfg/stock_2.cfg cfg/stock_3.cfg

不传参数时只体检当前配置文件。
"""

import os
import re
import sys
import unicodedata
from itertools import combinations

# 允许从项目根目录直接执行
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from utils.config import ENV_STOCK_CFG, load_stock_cfg, stock_instance_name  # noqa: E402


def pure_code(code: str) -> str:
    """取纯数字代码，避免 000001 / 000001.SZ / sh000001 被当成三只股票。"""
    matched = re.search(r"\d+", code or "")
    return matched.group() if matched else (code or "")


def _width(text: str) -> int:
    """计算终端显示宽度：中文等全角字符占 2 列。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad(text: str, target: int, align: str = "left") -> str:
    """按显示宽度补齐，避免中英文混排时列错位。"""
    space = max(target - _width(text), 0)
    return text + " " * space if align == "left" else " " * space + text


def scopes_of(cfg_path: str) -> dict:
    """算出单个实例的同步范围与容量参数。

    通过设置 Q_PROXY_CFG 让项目自身的配置读取逻辑指向目标文件，
    再复用 StockFetch 的取数方法，保证脚本与生产代码的口径一致。
    """
    os.environ[ENV_STOCK_CFG] = os.path.abspath(cfg_path)
    cp = load_stock_cfg()

    # 延迟导入：仅在真正体检时才引入 pandas / pymongo 等运行依赖
    from stock_fetch.stock_fetch import StockFetch

    stock_fetch = StockFetch()

    pool = {pure_code(s.get("code", "")) for s in stock_fetch.load_agent_pool_stocks() if s.get("code")}
    hot = {pure_code(c) for c in stock_fetch.get_hot_industry_stocks() if c}
    real_time_scope = pool | hot

    req_per_min = int(cp.get("tickflow", "max_requests_per_minute", fallback=9))
    symbols_per_req = int(cp.get("stock_fetch", "real_time_stock_limit", fallback=5))
    window_min = int(cp.get("stock_fetch", "minute_sync_interval", fallback=2))
    # 窗口内总采样点数：每分钟请求数 x 单次标的数 x 窗口长度（分钟）
    samples_per_window = req_per_min * symbols_per_req * window_min
    # 每只股票至少要有 2 条记录，相邻记录的累计量差值求和才不为 0
    max_stocks = samples_per_window // 2

    return {
        "cfg": cfg_path,
        "name": stock_instance_name(cp),
        "pool": pool,
        "hot": hot,
        "scope": real_time_scope,
        "samples_per_window": samples_per_window,
        "max_stocks": max_stocks,
    }


def main() -> int:
    cfg_paths = sys.argv[1:] or [os.environ.get(ENV_STOCK_CFG, "cfg/stock.cfg")]

    print("=" * 78)
    print("多实例分片体检")
    print("=" * 78)

    infos = []
    for cfg_path in cfg_paths:
        try:
            infos.append(scopes_of(cfg_path))
        except Exception as err:  # 单实例失败不影响其他实例的体检
            print(f"[跳过] {cfg_path}: {type(err).__name__}: {err}")
        finally:
            os.environ.pop(ENV_STOCK_CFG, None)

    if not infos:
        print("没有可体检的实例（配置文件读取或数据库连接失败）。")
        return 1

    print(f"\n共 {len(infos)} 个实例\n")

    print("-" * 78)
    print("一、各实例同步范围")
    print("-" * 78)
    print(
        pad("实例名", 20) + pad("配置文件", 20) + pad("股票池", 8, "right")
        + pad("热门行业", 10, "right") + pad("合计", 8, "right") + pad("上限", 8, "right")
    )
    for info in infos:
        over = "  ← 超出上限" if len(info["scope"]) > info["max_stocks"] else ""
        print(
            pad(info["name"], 20)
            + pad(os.path.basename(info["cfg"]), 20)
            + pad(str(len(info["pool"])), 8, "right")
            + pad(str(len(info["hot"])), 10, "right")
            + pad(str(len(info["scope"])), 8, "right")
            + pad(str(info["max_stocks"]), 8, "right")
            + over
        )

    print("\n  容量口径: 分钟窗口内采样点数 = 每分钟请求数 x 单次标的数 x 窗口分钟数;")
    print("            每只股票需至少 2 条记录，故单实例上限 = 采样点数 // 2")
    for info in infos:
        print(
            f"    {info['name']}: 采样点数 {info['samples_per_window']} → 上限 {info['max_stocks']} 只"
        )

    print("\n" + "-" * 78)
    print("二、实例间重叠")
    print("-" * 78)
    if len(infos) < 2:
        print("  只有一个实例，跳过重叠检查。")
    else:
        any_overlap = False
        for left, right in combinations(infos, 2):
            overlap = left["scope"] & right["scope"]
            if not overlap:
                print(f"  {left['name']} ∩ {right['name']}: 无重叠")
                continue
            any_overlap = True
            samples = sorted(overlap)
            print(f"  {left['name']} ∩ {right['name']}: {len(samples)} 只重叠")
            print(f"    {samples[:20]}{' ...' if len(samples) > 20 else ''}")
            only_hot = overlap - left["pool"] - right["pool"]
            if only_hot:
                print(f"    其中两边都只来自热门行业: {len(only_hot)} 只")

        # 统计被多个实例同时持有的 code
        seen: dict[str, list[str]] = {}
        for info in infos:
            for code in info["scope"]:
                seen.setdefault(code, []).append(info["name"])
        multi = {code: owners for code, owners in seen.items() if len(owners) > 1}
        total_scope = sum(len(i["scope"]) for i in infos)
        print(f"\n  去重后总股票数: {len(seen)}")
        print(f"  各实例范围之和: {total_scope}（重复采样 {total_scope - len(seen)} 只次）")
        if multi:
            detail = ", ".join(f"{code}({'+'.join(owners)})" for code, owners in sorted(multi.items())[:10])
            print(f"  被多实例同时同步: {len(multi)} 只 -> {detail}{' ...' if len(multi) > 10 else ''}")
        else:
            print("  分片互斥：没有 code 被两个实例同时同步。")

    print("\n" + "-" * 78)
    print("三、结论")
    print("-" * 78)
    problems = []
    for info in infos:
        if len(info["scope"]) > info["max_stocks"]:
            problems.append(
                f"{info['name']} 有 {len(info['scope'])} 只，超过单实例上限 {info['max_stocks']} 只，"
                f"分钟线 volume/amount 会偏小或为 0"
            )
    for left, right in combinations(infos, 2):
        overlap = left["scope"] & right["scope"]
        if overlap:
            problems.append(
                f"{left['name']} 与 {right['name']} 重叠 {len(overlap)} 只，"
                f"这些股票会被重复采样与重复推送"
            )
    if problems:
        for item in problems:
            print(f"  [需处理] {item}")
        print("\n  建议：按 code 哈希分片（shard_index / shard_total），")
        print("        在 get_real_time_stocks / get_industry_and_pool_stocks 返回前过滤，")
        print("        使每只股票只由一个实例负责。")
    else:
        print("  各实例均在容量上限内，且范围互不重叠，可直接按当前方式运行。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
