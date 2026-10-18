"""本地归档与服务端 HTTP 的取数对照。

为什么要单独做这一步：读取层一旦有 bug（列名错位、时间戳格式、排序、丢行），
回测和形态统计会静默算错而不会报错。HTTP 是独立来源，两者逐行比对是唯一能
证伪本地读取的方式。比对通过之前，本地数据不能用于任何结论。

用法（在 q_trading_work 根目录执行）：
    python _bt_local_vs_http.py --codes 000001.SZ,600519.SH \
        --local-dir ../q_trading_archive/data \
        --start 2026-09-01 --end 2026-10-14 --frequency daily

    # 分钟频，区间要落在分钟归档覆盖范围内
    python _bt_local_vs_http.py --codes 000001.SZ \
        --local-dir ../q_trading_archive/data \
        --start 2026-09-28 --end 2026-10-14 --frequency minute

退出码：0 = 全部一致；1 = 存在差异或本地读取失败。
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx
import pandas as pd

from api.client import ApiClient
from api.market import MarketApi
from dao.local_hq_dao import KIND_DAY, KIND_MINUTE, LocalHqDao

# 数值比较容差。HTTP 走 JSON、本地走 parquet 的 float64，同源数据不应有差异，
# 留极小余量兜住浮点表示与 JSON 解析的末位误差。
TOL = 1e-9

# 两端共有的键列，用于逐行对齐
KEY_DAY = "create_time"
KEY_MINUTE = "minute_time"


def _to_frame(records: list[dict]) -> pd.DataFrame:
    """把 list[dict] 转成 DataFrame，并统一时间列名。

    `_fetch_stock_data` 里对 `date` → `create_time` 做过兼容，说明某些接口返回
    的是 `date`。对照脚本必须做同样的归一化，否则会因为列名差异报出假的「不一致」。
    """
    frame = pd.DataFrame(records)
    if frame.empty:
        return frame
    if "date" in frame.columns and "create_time" not in frame.columns:
        frame["create_time"] = frame["date"]
    return frame


def _numeric_columns(frame: pd.DataFrame, exclude: set[str]) -> list[str]:
    cols: list[str] = []
    for name in frame.columns:
        if name in exclude:
            continue
        if pd.api.types.is_numeric_dtype(frame[name]):
            cols.append(name)
    return cols


def _fetch_remote(
    kind: str, code: str, start: str, end: str, token: str, retries: int = 2
) -> list[dict]:
    """取远端数据。

    **每次请求都新建客户端，不复用连接**。实测本项目的服务端对同一 TCP 连接上的
    第 2 个请求稳定返回 404（`{"detail":"Not Found"}`，响应体 22 字节）：裸
    `httpx.Client` 复用连接复现，顶层 `httpx.get`（每次新建连接）连打 5 次全部 200。
    用连接复用会把服务端这个缺陷伪装成「本地与服务端数据不一致」，方向完全错。

    另外分钟接口的时间过滤是字符串范围比较，`end` 传纯日期会静默返回 0 行，
    这里补全成全天范围。
    """
    if kind == KIND_MINUTE:
        remote_start = start if len(start) > 10 else f"{start} 00:00:00"
        remote_end = end if len(end) > 10 else f"{end} 23:59:59"
    else:
        remote_start, remote_end = start, end

    last: Exception | None = None
    for attempt in range(retries + 1):
        client = ApiClient()
        if token:
            client.set_token(token)
        api = MarketApi(client)
        try:
            if kind == KIND_MINUTE:
                return api.get_minute_kline(
                    code=code, start=remote_start, end=remote_end
                )
            return api.get_day_kline(code=code, start=remote_start, end=remote_end)
        except httpx.HTTPStatusError as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.0)
        finally:
            client.close()
    assert last is not None
    raise last


def compare_one(
    dao: LocalHqDao,
    kind: str,
    code: str,
    start: str,
    end: str,
    token: str,
) -> tuple[bool, list[str], list[str]]:
    """对照单只股票。

    :return: (是否一致, 差异列表, 提示列表)。提示列表装的是「正常但值得知道」
        的信息（例如远端多出的列），不影响结论。
    """
    messages: list[str] = []
    notes: list[str] = []

    remote_raw = _fetch_remote(kind, code, start, end, token)
    local_raw = dao.get_kline(kind, code, start, end)

    if not remote_raw:
        return True, [], [f"{code}: 服务端无数据，跳过对照"]
    if local_raw is None or not local_raw:
        return False, [f"{code}: 服务端 {len(remote_raw)} 行，本地 0 行"], notes

    remote = _to_frame(list(remote_raw))
    local = _to_frame(list(local_raw))
    key = KEY_MINUTE if kind == KIND_MINUTE else KEY_DAY

    if key not in remote.columns or key not in local.columns:
        return False, [
            f"{code}: 缺少时间列 {key}（远端={list(remote.columns)}，本地={list(local.columns)}）"
        ], notes

    remote[key] = remote[key].astype(str)
    local[key] = local[key].astype(str)
    remote = remote.sort_values(key, kind="stable").reset_index(drop=True)
    local = local.sort_values(key, kind="stable").reset_index(drop=True)

    if len(remote) != len(local):
        messages.append(f"{code}: 行数不同，远端 {len(remote)} / 本地 {len(local)}")
        remote_keys = set(remote[key])
        local_keys = set(local[key])
        only_remote = sorted(remote_keys - local_keys)[:5]
        only_local = sorted(local_keys - remote_keys)[:5]
        if only_remote:
            messages.append(f"    仅远端有: {only_remote}")
        if only_local:
            messages.append(f"    仅本地有: {only_local}")

    # 只比较两端都有的列。
    shared = [c for c in remote.columns if c in local.columns]
    extra_remote = sorted(set(remote.columns) - set(local.columns))
    extra_local = sorted(set(local.columns) - set(remote.columns))
    # 远端多出的列不算差异：归档刻意只保留回测需要的列，_id 是 mongo 内部字段，
    # name / qrr 冗余，change_amount 可由 close - preclose 推导。
    # 但本地多出的列要报出来：说明归档列与预期契约出现偏移。
    if extra_remote:
        notes.append(f"{code}: 远端多出 {len(extra_remote)} 列（归档不留，非差异）{extra_remote}")
    if extra_local:
        messages.append(f"{code}: 本地有而远端缺的列 {extra_local}")

    merged = remote[shared].merge(
        local[shared], on=key, how="outer", suffixes=("_r", "_l"), indicator=True
    )
    unmatched = merged[merged["_merge"] != "both"]
    if not unmatched.empty:
        messages.append(f"{code}: 有 {len(unmatched)} 个时间戳在两端对不上")
        for _, row in unmatched.head(5).iterrows():
            messages.append(f"    {row[key]}: {row['_merge']}")

    # 数值列逐列比对
    numeric = _numeric_columns(remote[shared], {key, "code"})
    for col in numeric:
        left = pd.to_numeric(remote[col], errors="coerce")
        right = pd.to_numeric(local[col], errors="coerce")
        if len(left) != len(right):
            # 行数不同时不做逐值比较，上面已经报过了
            continue
        diff = (left - right).abs()
        tolerance = TOL + left.abs() * 1e-12
        bad = diff > tolerance
        bad_count = int(bad.sum())
        if bad_count:
            messages.append(f"{code}: 列 {col} 有 {bad_count} 处不一致")
            for idx in bad[bad].index[:3]:
                messages.append(
                    f"    {remote[key].iloc[idx]}: 远端={left.iloc[idx]} 本地={right.iloc[idx]}"
                )

    return (not messages), messages, notes


def _load_admin_token() -> str:
    """读 cfg/stock.cfg 的 admin_token。

    对照脚本必须用与回测相同的认证方式。区间落在服务端窗口之外时它要走回源
    补齐，而回源有权限要求：不带 token 会拿到 403，很容易被误读成
    「服务端没有这部分数据」，进而得出「本地数据不可信」的反向结论。
    """
    try:
        from configparser import ConfigParser

        from utils.tools import resource_path

        cp = ConfigParser()
        cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
        return (cp.get("server", "admin_token", fallback="") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本地归档与 HTTP 取数对照")
    parser.add_argument("--codes", required=True, help="股票代码，逗号分隔")
    parser.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--local-dir", required=True, help="归档 parquet 根目录")
    parser.add_argument(
        "--frequency", default="daily", choices=("daily", "minute"), help="K线频率"
    )
    parser.add_argument(
        "--show-rows", type=int, default=0, help="额外打印前 N 行原始数据（调试用）"
    )
    args = parser.parse_args(argv)

    kind = KIND_MINUTE if args.frequency == "minute" else KIND_DAY
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]

    dao = LocalHqDao(args.local_dir)
    days = dao.available_days(kind)
    if not days:
        print(f"本地没有任何 {kind} 归档文件: {args.local_dir}", file=sys.stderr)
        return 1

    print(f"本地 {kind} 归档: {len(days)} 天（{days[0]} ~ {days[-1]}）")
    ok, reason = dao.coverage(kind, args.start, args.end)
    print(f"覆盖 {args.start} ~ {args.end}: {'完整' if ok else '不完整 ' + reason}")

    client = ApiClient()
    token = _load_admin_token()
    if token:
        print("已使用 cfg 的 admin_token")
    else:
        print(
            "警告：cfg 未配置 admin_token。区间出服务端窗口时回源会因权限返回 403，"
            "那是对照环境问题，不是数据不一致。",
            file=sys.stderr,
        )
    client.close()  # 只用来探一次认证配置，正式取数由 _fetch_remote 每次新建

    all_ok = True
    for code in codes:
        try:
            consistent, messages, notes = compare_one(
                dao, kind, code, args.start, args.end, token
            )
        except Exception as exc:  # noqa: BLE001 - 对照脚本要把异常报全，不中断
            all_ok = False
            print(f"{code}: 对照过程出错 {exc!r}")
            continue

        if consistent:
            print(f"{code}: 一致 ✓")
        else:
            all_ok = False
            print(f"{code}: 不一致 ✗")
        for line in notes:
            print(f"    {line}")
        for line in messages:
            print(f"    {line}")

        if args.show_rows:
            local_raw = dao.get_kline(kind, code, args.start, args.end) or []
            print(f"    本地前 {args.show_rows} 行: {local_raw[: args.show_rows]}")

    print("对照通过" if all_ok else "对照未通过")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
