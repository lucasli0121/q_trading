"""
Author: liguoqiang
Date: 2026-09-30
Description: 配置变更通知 —— 把 web 端的用户策略/股票池/分配改动推给 q_trading_work

背景：q_trading_work 只在进程启动那一刻读一次用户策略配置（`_init_workflow`
→ `load_strategies_from_db`），之后没有任何刷新机制。表现是「在界面改了策略
参数、改了股票池里的股票，跑着的实例完全不跟随」，要重启进程才生效。

服务端是唯一知道改动发生的地方，因此在写库成功后 publish 一条变更消息，
本端订阅后触发一次增量对账。

**载荷只用来「叫醒」与日志排查，本端不按 scope 分派**：收到任何一条都走同一条
对账路径（拉分配表 + 用户策略 + 模板，逐项对比后增量更新）。这样即使某个写入点
漏发通知，后果只是延迟到下一次轮询兜底，不会因为载荷字段不对而漏同步。

通知失败绝不影响业务：MQTT 未连接时只记 warning，接口照常返回成功。
"""

# coding="utf8"

import json
import logging
import time

from mq.mqtt_client import MqttTopic

logger = logging.getLogger(__name__)

# scope 取值（仅用于日志与排查，本端不依赖）
SCOPE_USER_STRATEGY: str = "user_strategy"
SCOPE_STRATEGY: str = "strategy"
SCOPE_POOL: str = "pool"
SCOPE_ASSIGNMENT: str = "assignment"
SCOPE_BLACKLIST: str = "blacklist"

# action 取值（同上）
ACTION_CREATE: str = "create"
ACTION_UPDATE: str = "update"
ACTION_DELETE: str = "delete"


def notify_config_change(scope: str, action: str, target: str = "") -> bool:
    """发布一条配置变更通知。

    :param scope: 变更对象类别（user_strategy / pool / assignment）
    :param action: 变更动作（create / update / delete）
    :param target: 变更对象标识（user_strategy_id / pool_id 或池名），仅用于日志
    :return: 是否发布成功（失败不影响调用方业务）
    """
    try:
        # 延迟导入：app_context 会拉起 MQTT/数据库等重资源，避免模块级循环依赖
        from app_context import AppContext

        payload: str = json.dumps(
            {
                "scope": scope,
                "action": action,
                "target": target,
                "ts": int(time.time()),
            },
            ensure_ascii=False,
        )
        ok: bool = AppContext().mqtt.publish(MqttTopic.CONFIG_CHANGE, payload)
        if ok:
            logger.info(
                "已发布配置变更通知 scope=%s action=%s target=%s",
                scope, action, target,
            )
        else:
            logger.warning(
                "配置变更通知发布失败（MQTT 未连接？）scope=%s action=%s target=%s",
                scope, action, target,
            )
        return ok
    except Exception as exc:
        # 通知只是加速手段，本端还有轮询兜底，绝不能因为它让写接口失败
        logger.warning(
            "配置变更通知异常 scope=%s action=%s target=%s: %s",
            scope, action, target, exc,
        )
        return False
