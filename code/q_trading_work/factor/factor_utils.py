# factor/factor_utils.py
from __future__ import annotations

from typing import Union

import numpy as np
import pandas as pd


class FactorUtils:
    """提供结构化数据的列提取工具，方便 ta-lib 计算。

    所有方法接收 np.ndarray 或 pd.DataFrame（含字段名如 close/open/high/low/volume），
    返回对应列的 float64 数组。
    """

    @staticmethod
    def price_array(stocks: pd.DataFrame) -> np.ndarray:
        """从结构化数据中提取价格列。

        :param stocks: 结构化 pd.DataFrame，含 price 字段
        :return: price 列的 float64 数组
        """
        return np.asarray(stocks["price"], dtype=np.float64)

    @staticmethod
    def close_array(stocks: pd.DataFrame) -> np.ndarray:
        """从结构化数据中提取收盘价列。

        :param stocks: 结构化 pd.DataFrame，含 close 字段
        :return: close 列的 float64 数组
        """
        return np.asarray(stocks["close"], dtype=np.float64)

    @staticmethod
    def low_array(stocks: pd.DataFrame) -> np.ndarray:
        """从结构化数据中提取最低价列。

        :param stocks: 结构化 pd.DataFrame，含 low 字段
        :return: low 列的 float64 数组
        """
        return np.asarray(stocks["low"], dtype=np.float64)

    @staticmethod
    def high_array(stocks: pd.DataFrame) -> np.ndarray:
        """从结构化数据中提取最高价列。

        :param stocks: 结构化 np.ndarray 或 pd.DataFrame，含 high 字段
        :return: high 列的 float64 数组
        """
        return np.asarray(stocks["high"], dtype=np.float64)

    @staticmethod
    def volume_array(stocks: pd.DataFrame) -> np.ndarray:
        """从结构化数据中提取成交量列。

        :param stocks: 结构化 np.ndarray 或 pd.DataFrame，含 volume 字段
        :return: volume 列的 float64 数组
        """
        return np.asarray(stocks["volume"], dtype=np.float64)

    @staticmethod
    def amount_array(stocks: pd.DataFrame) -> np.ndarray:
        """从结构化数据中提取成交额列。

        :param stocks: 结构化 np.ndarray 或 pd.DataFrame，含 amount 字段
        :return: amount 列的 float64 数组
        """
        return np.asarray(stocks["amount"], dtype=np.float64)
