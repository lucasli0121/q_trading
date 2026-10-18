"""类属性默认值必须与 cfg/strategy.yaml 的对应配置节一致。

无参数实例化（回测 CLI 的 `strategy_cls()`）走三级参数回退的第三级 —— yaml，
而读源码看到的是类属性。两者分叉时，「按代码推断实际生效参数」会静默出错：
2026-09-29 实测 `strong_rebound_strategy` 有 7 个参数如此（`BUY_MIN_SCORE` 4.0 vs 6.0、
`SELL_MAX_SCORE` −15.0 vs −8.0 等），`swing_trading_strategy` 有 3 个（窗口 5 vs 10）。
本用例把「一致」固化为不变量。

映射表只覆盖「类属性与 yaml 指向同一个值」的字段。yaml 未配置的字段（例如
`volume_expansion_strategy.buy.min_score` 之外的派生量）跳过 —— 此时类属性是唯一来源，
不存在分叉。
"""

from __future__ import annotations

import unittest
from typing import Any

import yaml

from strategy.strong_rebound_strategy import StrongReboundStrategy
from strategy.swing_trading_strategy import SwingTradingStrategy
from strategy.volume_expansion_strategy import VolumeExpansionStrategy
from utils.tools import resource_path

CFG_PATH: str = "cfg/strategy.yaml"

# {策略类: (yaml 节名, {(yaml 分组, yaml 字段): 类属性名})}
PARAM_MAPS: dict[type, tuple[str, dict[tuple[str, str], str]]] = {
    StrongReboundStrategy: (
        "strong_rebound_strategy",
        {
            ("match", "pe_limit"): "PE_LIMIT",
            ("match", "profit_limit"): "PROFIT_LIMIT",
            ("match", "trend_days_limit"): "TREND_DAYS_LIMIT",
            ("match", "consecutive_days"): "CONSECUTIVE_DAYS",
            ("match", "rebound_score"): "REBOUND_DAY_SCORE",
            ("match", "up_trend_score"): "UP_TREND_SCORE",
            ("buy", "consecutive_minutes"): "BUY_CONSECUTIVE_MINUTES",
            ("buy", "trend_minutes_limit"): "BUY_TREND_MINUTES_LIMIT",
            ("buy", "min_score"): "BUY_MIN_SCORE",
            ("sell", "trend_minutes_limit"): "SELL_TREND_MINUTES_LIMIT",
            ("sell", "vwap_minutes_limit"): "SELL_VWAP_LIMIT",
            ("sell", "drawdown_pct"): "SELL_DRAWDOWN_PCT",
            ("sell", "max_score"): "SELL_MAX_SCORE",
        },
    ),
    SwingTradingStrategy: (
        "swing_trading_strategy",
        {
            ("match", "sr_window"): "SR_WINDOW",
            ("match", "sr_tolerance"): "SR_TOLERANCE",
            ("match", "trend_days_limit"): "TREND_DAYS_LIMIT",
            ("match", "trend_adx_threshold"): "TREND_ADX_THRESHOLD",
            ("match", "trend_kline_days"): "TREND_KLINE_DAYS",
            ("buy", "buy_sr_window"): "BUY_SR_WINDOW",
            ("sell", "sell_sr_window"): "SELL_SR_WINDOW",
            ("sell", "drawdown_pct"): "SELL_DRAWDOWN_PCT",
            ("sell", "drawdown_score"): "SELL_DRAWDOWN_SCORE",
            ("risk", "max_drawdown_pct"): "RISK_MAX_DRAWDOWN_PCT",
            ("risk", "drawdown_score"): "SELL_RISK_DRAWDOWN_SCORE",
        },
    ),
    VolumeExpansionStrategy: (
        "volume_expansion_strategy",
        {
            ("match", "volume_days"): "VOLUME_DAYS",
            ("match", "volume_score"): "MATCH_VOLUME_SCORE",
            ("match", "partner_min_score"): "MATCH_PARTNER_MIN_SCORE",
            ("match", "partner_factor"): "MATCH_PARTNER_FACTOR",
            ("match", "volume_mode"): "MATCH_VOLUME_MODE",
            ("match", "quiet_volume_score"): "MATCH_QUIET_VOLUME_SCORE",
            ("buy", "min_minutes"): "BUY_MIN_MINUTES",
            ("buy", "volume_score"): "BUY_VOLUME_SCORE",
            ("buy", "partner_min_score"): "BUY_PARTNER_MIN_SCORE",
            ("buy", "partner_factor"): "BUY_PARTNER_FACTOR",
            ("buy", "volume_mode"): "BUY_VOLUME_MODE",
            ("buy", "quiet_volume_score"): "BUY_QUIET_VOLUME_SCORE",
            ("buy", "volume_weight"): "VOLUME_WEIGHT",
            ("buy", "partner_weight"): "PARTNER_WEIGHT",
            ("buy", "shape_enabled"): "SHAPE_ENABLED",
            ("buy", "shape_max_offset"): "SHAPE_MAX_OFFSET",
            ("buy", "shape_min_segments"): "SHAPE_MIN_SEGMENTS",
            ("buy", "shape_seq_enabled"): "SHAPE_SEQ_ENABLED",
            ("buy", "shape_seq_max_offset"): "SHAPE_SEQ_MAX_OFFSET",
            ("buy", "shape_seq_days"): "SHAPE_SEQ_DAYS",
            ("buy", "shape_ma_period"): "SHAPE_MA_PERIOD",
            ("buy", "shape_dive_score"): "SHAPE_DIVE_SCORE",
            ("buy", "shape_seq_rules"): "SHAPE_SEQ_RULES",
            # match.min_score 与 buy.min_score 映射到同一个 MIN_SCORE，apply_params
            # 中 buy 后写、以其为准，故此处只登记 buy 一侧。
            ("buy", "min_score"): "MIN_SCORE",
            ("sell", "min_minutes"): "SELL_MIN_MINUTES",
            ("sell", "max_score"): "SELL_MAX_SCORE",
            ("sell", "drawdown_pct"): "SELL_DRAWDOWN_PCT",
            ("sell", "vwap_minutes"): "SELL_VWAP_MINUTES",
            ("risk", "max_drawdown_pct"): "RISK_MAX_DRAWDOWN_PCT",
            ("risk", "drawdown_score"): "SELL_RISK_DRAWDOWN_SCORE",
        },
    ),
}


def _load_section(section: str) -> dict[str, Any]:
    """读取 cfg/strategy.yaml 的指定配置节，缺失时返回空字典。"""
    with open(resource_path(CFG_PATH), "r", encoding="utf-8") as f:
        config: Any = yaml.safe_load(f)
    data: Any = config.get(section) if isinstance(config, dict) else None
    return data if isinstance(data, dict) else {}


class TestParamDefaultsConsistency(unittest.TestCase):
    """策略类属性默认值 vs yaml 实际生效值。"""

    def test_yaml_sections_exist(self) -> None:
        """映射表引用的配置节必须真实存在，否则本用例会静默空转。"""
        for cls, (section, _mapping) in PARAM_MAPS.items():
            with self.subTest(strategy=cls.__name__):
                self.assertTrue(_load_section(section), f"{section} 配置节缺失或为空")

    def test_class_defaults_match_yaml(self) -> None:
        """逐字段比对：类属性默认值 == yaml 实际生效值。"""
        for cls, (section, mapping) in PARAM_MAPS.items():
            section_data: dict[str, Any] = _load_section(section)
            for (group, key), attr in mapping.items():
                group_data: Any = section_data.get(group) or {}
                if key not in group_data:
                    continue  # yaml 未配置，类属性是唯一来源
                with self.subTest(strategy=cls.__name__, field=f"{group}.{key}"):
                    self.assertEqual(
                        getattr(cls, attr),
                        group_data[key],
                        f"{cls.__name__}.{attr} 与 {section}.{group}.{key} 不一致；"
                        "无参数实例化（回测）实际用的是 yaml 值",
                    )
