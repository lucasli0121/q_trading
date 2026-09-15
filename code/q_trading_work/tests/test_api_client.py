"""
Author: liguoqiang
Date: 2026-09-15
Description: ApiClient 错误日志级别测试 —— 校验 4xx/5xx 的日志策略：

    - 默认：非 401 的 4xx/5xx 记 ERROR 并带上响应体（定位字段级错误用，
      2026-09-15 排查 trade_signal 422 时就是缺这一条）
    - `allow_not_found=True`：404 降为 DEBUG（调用方已声明「资源不存在」是
      正常业务状态，如某用户策略尚无执行记录），避免良性 404 污染 errors.log
    - 两种情况下异常都照旧抛出，兜底责任仍归调用方

全部使用 mock，不连服务端。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import httpx

from api.client import ApiClient
from api.user_strategy import UserStrategyApi


class _StubConfig:
    """ApiClient 只用到 base_url，用最小替身避免读 cfg 文件。"""

    base_url: str = "http://127.0.0.1:1"


def _resp(status: int, payload: dict) -> httpx.Response:
    """构造带 request 的响应（raise_for_status 依赖 request 才能抛出）。"""
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("GET", "http://127.0.0.1:1/api/x"),
    )


class TestErrorBodyLogging(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ApiClient(_StubConfig())  # type: ignore[arg-type]
        self.client._client = MagicMock()  # type: ignore[assignment]

    def test_422_logs_error_with_response_body(self) -> None:
        """默认行为：422 记 ERROR 且带响应体（不许退化，否则又查不出字段）。"""
        self.client._client.get.return_value = _resp(
            422, {"detail": [{"loc": ["body", "reason"], "msg": "string_type"}]}
        )
        with self.assertLogs("api.client", level="DEBUG") as cm:
            with self.assertRaises(httpx.HTTPStatusError):
                self.client.get("/api/x")

        errors = [r for r in cm.records if r.levelname == "ERROR"]
        self.assertEqual(len(errors), 1)
        self.assertIn("422", errors[0].getMessage())
        self.assertIn("string_type", errors[0].getMessage())

    def test_404_without_flag_logs_error(self) -> None:
        """未声明可接受的 404 仍按 error 记录（默认不放松）。"""
        self.client._client.get.return_value = _resp(
            404, {"detail": "未找到该策略的执行结果"}
        )
        with self.assertLogs("api.client", level="DEBUG") as cm:
            with self.assertRaises(httpx.HTTPStatusError):
                self.client.get("/api/x")

        errors = [r for r in cm.records if r.levelname == "ERROR"]
        self.assertEqual(len(errors), 1)
        self.assertIn("404", errors[0].getMessage())

    def test_404_with_flag_logs_debug_only_and_still_raises(self) -> None:
        """allow_not_found=True：只记 DEBUG，不再产生 ERROR，但异常照旧抛出。"""
        self.client._client.get.return_value = _resp(
            404, {"detail": "未找到该策略的执行结果"}
        )
        with self.assertLogs("api.client", level="DEBUG") as cm:
            with self.assertRaises(httpx.HTTPStatusError):
                self.client.get("/api/x", allow_not_found=True)

        self.assertNotIn("ERROR", [r.levelname for r in cm.records])
        self.assertTrue(
            any("可接受" in r.getMessage() for r in cm.records),
            "应留下一条说明「调用方声明为可接受」的 DEBUG 记录",
        )

    def test_flag_does_not_swallow_other_4xx(self) -> None:
        """allow_not_found 只作用于 404，其他 4xx 依旧记 ERROR。"""
        self.client._client.get.return_value = _resp(400, {"detail": "bad request"})
        with self.assertLogs("api.client", level="DEBUG") as cm:
            with self.assertRaises(httpx.HTTPStatusError):
                self.client.get("/api/x", allow_not_found=True)

        errors = [r for r in cm.records if r.levelname == "ERROR"]
        self.assertEqual(len(errors), 1)
        self.assertIn("400", errors[0].getMessage())


class TestGetLatestExecutionContract(unittest.TestCase):
    def test_declares_404_acceptable(self) -> None:
        """get_latest_execution 必须显式声明 404 可接受（服务端用它表示无记录）。"""
        client = MagicMock()
        client.get.return_value = None
        api = UserStrategyApi(client)

        self.assertEqual(api.get_latest_execution("us-1"), {})

        client.get.assert_called_once_with(
            "/api/user_strategy/us-1/execution/latest", allow_not_found=True
        )

    def test_returns_record_when_present(self) -> None:
        client = MagicMock()
        client.get.return_value = {"initial_amount": 100000.0}
        api = UserStrategyApi(client)

        self.assertEqual(api.get_latest_execution("us-1"), {"initial_amount": 100000.0})


if __name__ == "__main__":
    unittest.main()
