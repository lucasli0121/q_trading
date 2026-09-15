"""
Author: liguoqiang
Date: 2026-09-15
Description: 交易信号 API 测试 —— 锁定 POST /api/trade_signal/add 的请求体类型契约。

背景（2026-09-15 线上 422）：
    `swing_trading_strategy._check_sell_conditions` 曾把
        extra["reason"] = (f"...条件满足..."),
    写成了**单元素 tuple**（多一个逗号）。`_save_trade_signal` 原样透传后，
    JSON 序列化成数组，服务端 `TradeSignalCreateRequest.reason: str` 校验失败：

        HTTP 422 {"errors":[{"type":"string_type","loc":["body","reason"],...}]}

    整条交易信号（含 MQTT 推送与订单创建判定）被全部丢弃，且日志里只有
    "422 Unprocessable Entity"，看不出是哪个字段。

本文件锁定两点：
1. `TradeSignalApi.add` 在 API 边界把 str 字段归一化，脏值不再变成 422；
2. 用 AST 静态检查兜住「dict 取值被赋值成单元素 tuple」这个静默 bug 类。

全部使用 mock client，不连服务端。
"""

from __future__ import annotations

import ast
import json
import os
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from api.trade_signal import TradeSignalApi, _as_text


class TestAsText(unittest.TestCase):
    """`_as_text` 归一化语义。"""

    def test_str_passthrough(self) -> None:
        self.assertEqual(_as_text("买入条件合格"), "买入条件合格")

    def test_none_becomes_empty(self) -> None:
        self.assertEqual(_as_text(None), "")

    def test_single_element_tuple_is_flattened(self) -> None:
        """线上真实 bug 的形态：单元素 tuple 必须还原成字符串。"""
        self.assertEqual(_as_text(("条件满足: 分值 -1.20",)), "条件满足: 分值 -1.20")

    def test_list_and_nested(self) -> None:
        self.assertEqual(_as_text(["a", "b"]), "a, b")
        self.assertEqual(_as_text(("a", ("b", "c"))), "a, b, c")

    def test_other_types_use_str(self) -> None:
        self.assertEqual(_as_text(123), "123")
        self.assertEqual(_as_text(0), "0")


class TestTradeSignalAddContract(unittest.TestCase):
    """POST /api/trade_signal/add 的请求体契约。"""

    def _api(self) -> tuple[TradeSignalApi, MagicMock]:
        client = MagicMock()
        client.post.return_value = {"id": "sig-1"}
        return TradeSignalApi(client), client

    def test_add_posts_expected_body(self) -> None:
        api, client = self._api()

        api.add(
            strategy_id="sid",
            user_strategy_id="us1",
            stock_code="000001",
            trade_price=10.5,
            action="卖出",
            profit_rate=12.34,
            reason="条件满足",
        )

        client.post.assert_called_once_with(
            "/api/trade_signal/add",
            {
                "strategy_id": "sid",
                "user_strategy_id": "us1",
                "stock_code": "000001",
                "trade_price": 10.5,
                "action": "卖出",
                "profit_rate": 12.34,
                "profit_amount": 0.0,
                "reason": "条件满足",
            },
        )

    def test_add_flattens_tuple_reason(self) -> None:
        """回归：tuple 形态的 reason 不应再产生 422 请求体。"""
        api, client = self._api()

        api.add(
            strategy_id="sid",
            user_strategy_id="us1",
            stock_code="000001",
            trade_price=10.5,
            action="卖出",
            reason=("条件满足: 布林带分值 -1.20",),  # type: ignore[arg-type]
        )

        body: dict[str, Any] = client.post.call_args.args[1]
        self.assertIsInstance(body["reason"], str)
        self.assertEqual(body["reason"], "条件满足: 布林带分值 -1.20")

    def test_add_normalizes_none_and_non_str_fields(self) -> None:
        """strategy_id/action 为 None、stock_code 为数字时也要能正常提交。"""
        api, client = self._api()

        api.add(
            strategy_id=None,  # type: ignore[arg-type]
            user_strategy_id=None,  # type: ignore[arg-type]
            stock_code=1,  # type: ignore[arg-type]
            trade_price=10.5,
            action=None,  # type: ignore[arg-type]
            reason=None,
        )

        body: dict[str, Any] = client.post.call_args.args[1]
        for key in ("strategy_id", "user_strategy_id", "stock_code", "action", "reason"):
            self.assertIsInstance(body[key], str, msg=f"{key} 应为 str")
        self.assertEqual(body["stock_code"], "1")

    def test_add_body_has_no_container_typed_text_fields(self) -> None:
        """请求体必须是纯 JSON 标量，且可被 json 序列化（服务端 str 字段不接受数组）。"""
        api, client = self._api()

        api.add(
            strategy_id="sid",
            user_strategy_id="us1",
            stock_code="000001",
            trade_price=10.5,
            action="买入",
            reason=("a", "b"),  # type: ignore[arg-type]
        )

        body: dict[str, Any] = client.post.call_args.args[1]
        for key, value in body.items():
            self.assertNotIsInstance(
                value, (list, tuple, dict), msg=f"{key} 不应是容器类型"
            )
        json.dumps(body)  # 不抛异常即可


class TestNoAccidentalTupleAssignment(unittest.TestCase):
    """静态兜底：禁止把「本该是文本」的字典值写成单元素 tuple。

    这类错误在 Python 里完全合法（只是类型变了），单测/联调都发现不了，
    却会在请求序列化时变成 JSON 数组并被服务端 422 拒绝 —— 本次线上故障
    正是如此（`extra["reason"] = (f"..."),`）。因此用 AST 静态锁住：

        d["key"] = ("文本")      # ✅ 隐式拼接/普通字符串
        d["key"] = ("文本",)     # ❌ 单元素 tuple，几乎不可能是本意

    只检查「下标赋值」这一种形态，避免误伤合法的元组常量定义。
    """

    SCAN_DIRS: tuple[str, ...] = ("strategy", "workflow", "api", "dao", "backtest")

    def test_no_single_element_tuple_assigned_to_subscript(self) -> None:
        root: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        offenders: list[str] = []

        for scan_dir in self.SCAN_DIRS:
            base: str = os.path.join(root, scan_dir)
            if not os.path.isdir(base):
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                for filename in filenames:
                    if not filename.endswith(".py"):
                        continue
                    path: str = os.path.join(dirpath, filename)
                    with open(path, encoding="utf-8") as fh:
                        source: str = fh.read()
                    try:
                        tree: ast.AST = ast.parse(source)
                    except SyntaxError:  # pragma: no cover - 防御性
                        continue
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Assign):
                            value: ast.expr | None = node.value
                            targets: list[ast.expr] = list(node.targets)
                        elif isinstance(node, ast.AnnAssign):
                            value = node.value
                            targets = [node.target]
                        else:
                            continue
                        if value is None or not isinstance(value, ast.Tuple):
                            continue
                        if len(value.elts) != 1:
                            continue
                        element: ast.expr = value.elts[0]
                        is_text: bool = (
                            (isinstance(element, ast.Constant) and isinstance(element.value, str))
                            or isinstance(element, ast.JoinedStr)
                            or isinstance(element, ast.BinOp)
                        )
                        if not is_text:
                            continue
                        if not any(isinstance(t, ast.Subscript) for t in targets):
                            continue
                        offenders.append(
                            f"{os.path.relpath(path, root)}:{node.lineno} "
                            f"{ast.unparse(node)[:90]}"
                        )

        self.assertEqual(
            offenders,
            [],
            msg=(
                "发现「字典值被赋值成单元素 tuple」的写法，JSON 序列化后会变成数组"
                "（服务端 str 字段会 422）。请删除多余逗号：\n" + "\n".join(offenders)
            ),
        )


class TestStrategySignalPayloadContract(unittest.TestCase):
    """策略侧产出的信号字段类型契约（根因回归）。

    线上 422 的源头在策略里：`swing_trading_strategy._check_sell_conditions`
    的卖出命中分支把 `extra["reason"]` 写成了单元素 tuple。这里直接锁住
    「策略产出的 reason 必须是 str」，避免同类写法再次悄悄溜进信号链路。
    """

    def test_swing_sell_hit_reason_is_str(self) -> None:
        import pandas as pd

        import strategy.swing_trading_strategy as swing_mod

        class _NegFactor:
            """假因子：恒返回负值，令卖出条件（raise_score >= 2）必然命中。"""

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.value: float = -1.0

            def score(self, df: Any) -> float:
                return self.value

            def score_with_sr(self, sr_df: Any, stock_df: Any) -> float:
                return self.value

        strategy = swing_mod.SwingTradingStrategy()
        with (
            patch.object(swing_mod, "InDaySRFactor", _NegFactor),
            patch.object(swing_mod, "BollSRFactor", _NegFactor),
            patch.object(strategy, "_get_minute_cache", return_value=[{"price": 10.5}]),
            patch.object(strategy, "_get_sr_points", return_value=pd.DataFrame()),
        ):
            matched, extra = strategy._check_sell_conditions(
                "000001",
                {},
                {
                    "price": 10.5,
                    "preclose": 10.4,
                    "create_time": "2026-09-15 10:00:00",
                },
            )

        self.assertTrue(matched, msg=f"卖出条件应命中，实际 extra={extra!r}")
        self.assertIsInstance(extra["reason"], str)
        self.assertTrue(extra["reason"])


if __name__ == "__main__":
    unittest.main()
