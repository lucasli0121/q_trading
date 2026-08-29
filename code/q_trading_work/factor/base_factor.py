# factor/base_factor.py

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Union

import numpy as np
import pandas as pd


class BaseFactor(ABC):
    """
    因子基类

    所有因子必须继承该类
    """

    factor_name: str = "BaseFactor"

    @abstractmethod
    def calculate(
        self,
        stocks: pd.DataFrame,
    ) -> Any:
        """计算因子值。

        :param stocks: 结构化 np.ndarray 或 pd.DataFrame，含 close/open/high/low/volume 等字段
        :return: 因子计算结果
        """
        raise NotImplementedError

    @abstractmethod
    def score(self, stocks: pd.DataFrame, total_score: float = 10.0) -> float:
        """根据因子值进行打分。

        :param stocks: 结构化 np.ndarray 或 pd.DataFrame，含 close/open/high/low/volume 等字段
        :return: 分数 <= 10.0
        """
        raise NotImplementedError