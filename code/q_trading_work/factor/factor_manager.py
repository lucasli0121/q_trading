# factor/factor_manager.py

from __future__ import annotations

from typing import Optional

from factor.base_factor import BaseFactor


class FactorManager:
    """
    因子管理器

    用于：

    - 注册因子
    - 获取因子
    """

    def __init__(self) -> None:

        self.factor_map: dict[
            str,
            BaseFactor
        ] = {}

    def register(
        self,
        name: str,
        factor: BaseFactor
    ) -> None:
        """
        注册因子
        """

        self.factor_map[name] = factor

    def get(
        self,
        name: str
    ) -> Optional[BaseFactor]:
        """
        获取因子

        Parameters
        ----------
        name : str
            因子名称

        Returns
        -------
        Optional[BaseFactor]
            因子对象
        """

        return self.factor_map.get(name)