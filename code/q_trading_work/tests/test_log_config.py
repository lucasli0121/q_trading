"""日志配置守卫测试（2026-10-09）。

锁住 `cfg/log.yaml` 的三条不变量，防止「为了看 DEBUG 而把 root 调成 DEBUG」
这类改法回归：
  ① root 保持 INFO —— root 改 DEBUG 会把 httpcore / httpx / pymongo /
     apscheduler 的 DEBUG 一起灌进 info.log（实测稳态一轮对账从 13 行涨到 144 行，
     其中 120 行是 httpcore.http11 的报文级细节）。
  ② 业务包（workflow）单独放行 DEBUG，且 propagate=false —— 否则记录冒泡到
     root 的 handlers 会重复输出一遍。
  ③ loggers.workflow.handlers 必须列全，含 error_file_handler ——
     propagate=false 之后不再借用 root 的 handler 列表，少列就让 ERROR 进不了
     errors.log。
  ④ 噪音库钉在 WARNING —— 即使有人把 root 临时调成 DEBUG，也不会被刷屏。
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

LOG_YAML: Path = Path(__file__).resolve().parent.parent / "cfg" / "log.yaml"


def load_config() -> dict:
    with open(LOG_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestLogConfig(unittest.TestCase):
    """cfg/log.yaml 的结构不变量。"""

    def setUp(self) -> None:
        self.cfg: dict = load_config()

    def test_root_level_stays_info(self) -> None:
        """root 必须是 INFO。改成 DEBUG 会连带放行所有第三方库。"""
        self.assertEqual(
            self.cfg["root"]["level"].upper(),
            "INFO",
            "root 不要设成 DEBUG：业务 DEBUG 请用 loggers.workflow 单独放行，"
            "否则 httpcore / httpx / pymongo / apscheduler 的 DEBUG 会一起进 info.log",
        )

    def test_workflow_package_is_released_for_debug(self) -> None:
        """业务包放行 DEBUG，且不冒泡（否则同一行会输出两遍）。"""
        wf = self.cfg["loggers"]["workflow"]
        self.assertEqual(wf["level"].upper(), "DEBUG")
        self.assertFalse(
            wf.get("propagate", True),
            "propagate 必须显式 false，否则记录会冒泡到 root 的 handlers 重复输出",
        )

    def test_workflow_handlers_are_complete(self) -> None:
        """propagate=false 后必须自带全部 handler，否则 ERROR 进不了 errors.log。"""
        declared = set(self.cfg["handlers"].keys())
        for name in self.cfg["loggers"]["workflow"]["handlers"]:
            self.assertIn(name, declared, f"loggers.workflow 引用了未定义的 handler: {name}")
        self.assertIn("error_file_handler", self.cfg["loggers"]["workflow"]["handlers"])
        self.assertIn("info_file_handler", self.cfg["loggers"]["workflow"]["handlers"])

    def test_info_handler_not_restricted_to_info(self) -> None:
        """info handler 不能把闸门设在 INFO，否则 workflow 的 DEBUG 写不进 info.log。"""
        self.assertEqual(self.cfg["handlers"]["info_file_handler"]["level"].upper(), "DEBUG")

    def test_noisy_libraries_are_pinned(self) -> None:
        """噪音库钉在 WARNING：root 万一被调成 DEBUG，也不会被报文级细节刷屏。"""
        for name in ("httpcore", "urllib3", "pymongo"):
            self.assertIn(name, self.cfg["loggers"], f"{name} 未钉级别，root=DEBUG 时会被刷屏")
            self.assertEqual(
                self.cfg["loggers"][name]["level"].upper(),
                "WARNING",
                f"{name} 应为 WARNING",
            )

    def test_httpx_and_apscheduler_remain_visible(self) -> None:
        """httpx 的请求流水与 apscheduler 的 job 行是排查依据，不应被静音。"""
        for name in ("httpx", "apscheduler"):
            level = self.cfg["loggers"].get(name, {}).get("level", "NOTSET")
            self.assertNotIn(
                level.upper(),
                ("WARNING", "ERROR", "CRITICAL"),
                f"{name} 被静音会导致看不到请求流水 / 对账是否在跑",
            )

    def test_all_handlers_are_referenced(self) -> None:
        """每个定义的 handler 都应被 root 或某个 logger 使用（防止配置写了一半）。"""
        used: set[str] = set(self.cfg["root"]["handlers"])
        for lg in self.cfg["loggers"].values():
            used.update(lg.get("handlers", []))
        for name in self.cfg["handlers"]:
            self.assertIn(name, used, f"handler {name} 定义了却没被任何 logger 使用")


if __name__ == "__main__":
    unittest.main()
