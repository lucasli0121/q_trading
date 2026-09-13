
import logging
from datetime import datetime
from typing import Any

from nicegui import app

from core.singleton import Singleton

logger = logging.getLogger(__name__)


class UserStrategyManager(metaclass=Singleton):
    """用户策略管理器。
     初始化时首次读取数据库，加载用户对应的策略信息到app.storage
     保存到app.storage.user["user_strategies"]中，数据结构为列表，每个元素为一个字典，包含用户策略的详细信息。
     目的是保存到用户各自的存储空间
    """

    def __init__(self) -> None:
        app.storage.user["user_strategies"] = []

    def load_user_strategies(self,) -> None:
        from api.client import UnauthorizedError
        from app_context import AppContext
        try:
            strategies = AppContext().user_strategy_api.list()
        except UnauthorizedError:
            # 401 已由 ApiClient._on_401 清除会话（authenticated=False），
            # 心跳定时器（main_page._check_auth_heartbeat）会跳转登录页；
            # 此处静默返回避免异常冒泡中断页面渲染。
            logger.debug("加载用户策略未授权（401），会话已过期，等待跳转登录")
            return
        except Exception:
            logger.warning("加载用户策略失败", exc_info=True)
            return
        if app.is_started:
            app.storage.user["user_strategies"] = strategies

    def get_user_strategy(self, user_strategy_ids: list) -> dict[str, Any] | None:
        """获取指定用户策略的详细信息。

        :param user_strategy_ids: 用户策略 ID 列表
        :return: 用户策略详细信息，若不存在则返回 None
        """
        if app.is_started:
            return {id: strategy for id in user_strategy_ids for strategy in app.storage.user["user_strategies"] if str(strategy.get("_id", strategy.get("id", ""))) == id}
        return None

    def list_user_strategies(self) -> list[dict[str, Any]]:
        """列出所有用户策略。

        :return: 用户策略列表
        """
        if app.is_started:
            return app.storage.user["user_strategies"]
        return []
    def update_user_strategy(self, user_strategy_id: str, strategy_info: dict[str, Any]) -> None:
        """更新指定用户策略的信息。

        :param user_strategy_id: 用户策略 ID
        :param strategy_info: 用户策略详细信息
        """
        if app.is_started:
            has_updated = False
            for i, strategy in enumerate(app.storage.user["user_strategies"]):
                if str(strategy.get("_id", strategy.get("id", ""))) == user_strategy_id:
                    app.storage.user["user_strategies"][i] = strategy_info
                    has_updated = True
                    break
            if not has_updated:
                app.storage.user["user_strategies"].append(strategy_info)

    def remove_user_strategy(self, user_strategy_id: str) -> None:
        """移除指定用户策略。

        :param user_strategy_id: 用户策略 ID
        """
        if app.is_started:
            app.storage.user["user_strategies"] = [
                strategy for strategy in app.storage.user["user_strategies"]
                if str(strategy.get("_id", strategy.get("id", ""))) != user_strategy_id
            ]

    def get_user_strategy_ids(self) -> list[str]:
        """获取所有用户策略的 ID 列表。

        :return: 用户策略 ID 列表
        """
        if app.is_started:
            return [str(strategy.get("_id", strategy.get("id", ""))) for strategy in app.storage.user["user_strategies"]]
        return []

    def get_running_count(self) -> int:
        """获取当前正在运行的用户策略数量。

        :return: 正在运行的用户策略数量
        """
        if app.is_started:
            return sum(1 for strategy in app.storage.user["user_strategies"] if strategy.get("status") == "running")
        return 0

    def get_strategy_count(self) -> int:
        """获取当前用户策略总数。

        :return: 用户策略总数
        """
        if app.is_started:
            return len(app.storage.user["user_strategies"])
        return 0
    
    def get_month_new_count(self) -> int:
        """获取当前月份新增的用户策略数量。

        :return: 当前月份新增的用户策略数量
        """
        if app.is_started:
            month_prefix: str = datetime.now().astimezone().strftime("%Y-%m")
            return sum(1 for strategy in app.storage.user["user_strategies"] if strategy.get("create_time", "").startswith(month_prefix))
        return 0