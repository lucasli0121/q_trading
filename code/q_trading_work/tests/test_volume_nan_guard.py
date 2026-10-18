"""量比 NaN 被当成满分买入信号（2026-10-14 修复）的守卫。

失效链条：六项分项全部依赖区间比较（`ratio >= 3.0`、`slope > 0.0`、`obv > obv_ma` …），
NaN 参与比较恒为 False ⇒ 各项都不计分 ⇒ 总分落在 0 附近。而买入侧 reversion 模式的
「量能强度」= (缩量上限 − 成交量分) / 缩量上限，**成交量分为 0 时恰好给满分** ——
于是「算不出来」被判成「极度缩量」这个最理想买点。

实测（2026-07-21~10-13 分钟频，成交量/自选池）：405/4110 次买入信号含 NaN，
落地的 5 笔全部亏损，平均 −3.87%（正常组 −1.54%）。

本文件守住三件事：
1. 因子层：关键输入 NaN ⇒ `data_ok=False`，而不是「安静地给 0 分」；
2. 策略层：`_combine(data_valid=False)` 直接否决，两个分量归零；
3. 「0 分」与「算不出来」必须区分开 —— 0 分是合法的真·缩量，仍应给满分强度。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest

from core.user_strategy_cache import UserStrategyCache
from factor.volume_expansion_factor import VolumeExpansionFactor
from strategy.volume_expansion_strategy import MODE_REVERSION, VolumeExpansionStrategy

PROJECT_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]

USID: str = "test-volume-nan-guard"

_PARAMS: dict[str, Any] = {
    "match": {
        "volume_days": 20,
        "volume_score": 4.0,
        "partner_min_score": 0.0,
        "min_score": 5.0,
        "partner_factor": "boll_sr",
        "volume_mode": "momentum",
        "quiet_volume_score": 4.0,
    },
    "buy": {
        "min_minutes": 20,
        "volume_score": 4.0,
        "partner_min_score": 0.0,
        "min_score": 5.0,
    },
    "sell": {"min_minutes": 20, "max_score": -6.0},
    "risk": {},
}


def _frame(count: int = 30, volume_nan: bool = False) -> pd.DataFrame:
    """构造因子输入：`volume_nan=True` 时成交量全为 NaN（模拟行情缺失）。"""
    rows: list[dict[str, float]] = []
    for i in range(count):
        rows.append(
            {
                "close": 10.0 + i * 0.1,
                "high": 10.2 + i * 0.1,
                "low": 9.8 + i * 0.1,
                "volume": float("nan") if volume_nan else 1000.0 + i * 10.0,
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture()
def strategy() -> VolumeExpansionStrategy:
    cache = UserStrategyCache()
    cache.set_redis_enabled(False)
    instance = VolumeExpansionStrategy(
        user_strategy_id=USID, user_strategy_params=_PARAMS
    )
    yield instance
    cache.set_redis_enabled(True)


class TestFactorNanGuard:
    """因子层：输入缺失必须显式标注，不能安静地给 0 分。"""

    def test_nan_input_marks_data_not_ok(self) -> None:
        result: dict[str, Any] = VolumeExpansionFactor(days=5).score(_frame(volume_nan=True))
        assert result["data_ok"] is False
        assert result["signal"] == "None"
        assert result["score"] == 0.0
        # reason 必须写出缺了什么，否则线上只能靠反推
        assert "数据缺失" in "".join(result["reason"])

    def test_short_input_marks_data_not_ok(self) -> None:
        """行数不足是另一种「算不出来」，同样标注 False。"""
        result: dict[str, Any] = VolumeExpansionFactor(days=20).score(_frame(count=10))
        assert result["data_ok"] is False

    def test_normal_input_is_ok(self) -> None:
        result: dict[str, Any] = VolumeExpansionFactor(days=5).score(_frame())
        assert result["data_ok"] is True

    def test_missing_field_listed(self) -> None:
        """缺哪些字段要逐个列出来（便于区分「行情没回」与「指标还没预热」）。"""
        result: dict[str, Any] = VolumeExpansionFactor(days=5).score(_frame(volume_nan=True))
        reason: str = "".join(result["reason"])
        for field in ("量比", "成交量斜率"):
            assert field in reason


class TestCombineRejectsInvalidData:
    """策略层：数据缺失直接否决，不拿 0 分去比门槛。"""

    def test_invalid_data_rejects(self, strategy: VolumeExpansionStrategy) -> None:
        volume_ok, partner_ok, combined, vol_component, partner_component = strategy._combine(
            0.0, 9.0, "boll_sr", MODE_REVERSION, 4.0, 0.0, 4.0, data_valid=False
        )
        assert volume_ok is False
        assert partner_ok is False
        assert combined == 0.0
        # 关键：量能强度不得因为「分数为 0」而被算成满分
        assert vol_component == 0.0
        assert partner_component == 0.0

    def test_zero_score_with_valid_data_keeps_full_strength(
        self, strategy: VolumeExpansionStrategy
    ) -> None:
        """0 分是合法的真·极缩量（data_valid=True）⇒ 仍给满分强度，行为不变。"""
        volume_ok, partner_ok, combined, vol_component, _ = strategy._combine(
            0.0, 9.0, "boll_sr", MODE_REVERSION, 4.0, 0.0, 4.0, data_valid=True
        )
        assert volume_ok is True
        assert partner_ok is True
        assert vol_component == 1.0
        assert combined > 0.0

    def test_buy_path_rejects_nan_volume(self, strategy: VolumeExpansionStrategy) -> None:
        """端到端：分钟缓存里成交量全 NaN ⇒ 买入判定不通过。"""
        cache = UserStrategyCache()
        bar: dict[str, Any] = {
            "code": "002050.SZ",
            "name": "三花智控",
            "price": 30.0,
            "open": 30.0,
            "preclose": 30.0,
            "high": 30.5,
            "low": 29.5,
            "close": 30.0,
            "volume": float("nan"),
            "amount": 0.0,
            "create_time": "2026-09-25 10:30:00",
        }
        for i in range(25):
            cache.push_minute_bar("002050.SZ", {**bar, "create_time": f"2026-09-25 10:{i:02d}:00"})
        try:
            matched, extra = strategy._check_buy_conditions("002050.SZ", bar)
        finally:
            cache.clear_user(USID)
        assert matched is False
        # 必须是因为「数据缺失」被挡，而不是恰好分值不够
        assert "成交量" in str(extra.get("reason", "")) or "不达标" in str(extra.get("reason", ""))

    def test_call_sites_pass_data_valid(self) -> None:
        """源码守卫：两个 `_combine` 调用点都必须把 data_ok 传进去。

        漏传一处 = 该路径上的 NaN 仍会被读成满分买点，而另一条路径看起来正常。
        """
        src: str = (PROJECT_ROOT / "strategy" / "volume_expansion_strategy.py").read_text(
            encoding="utf-8"
        )
        assert src.count("data_valid=volume_data_ok") == 2
        assert 'bool(volume.get("data_ok", True))' in src


class TestVolumeDetailPropagatesDataOk:
    """`_volume_detail` 的容错分支必须保留 data_ok 语义。"""

    def test_exception_returns_data_not_ok(self, strategy: VolumeExpansionStrategy) -> None:
        with patch.object(strategy, "_volume_factor", side_effect=RuntimeError("boom")):
            detail: dict[str, Any] = strategy._volume_detail(_frame())
        # 计算失败同样属于「算不出来」：返回的 score=0 不能冒充「真·极缩量」
        assert detail.get("data_ok") is False

    def test_factor_result_passthrough(self, strategy: VolumeExpansionStrategy) -> None:
        with patch.object(strategy, "_volume_factor") as mocked:
            mocked.return_value.score.return_value = {
                "score": 3.0,
                "signal": "None",
                "reason": [],
                "data_ok": False,
            }
            detail: dict[str, Any] = strategy._volume_detail(_frame())
        assert detail["data_ok"] is False
