"""
Author: liguoqiang
Date: 2026-09-30
Description: 系统消息「用户已读标记」单元测试

不依赖 MongoDB / Redis：MongoSystemMessageImpl 用 __new__ 构造后注入 collection 替身，
路由层用替身 impl 覆盖模块属性。覆盖三件事：
  1) 可见性与读状态判定（广播 vs 定向、is_read 与已读数）
  2) mark_read 的写入形态与幂等性（$addToSet，不覆盖整数组）
  3) POST /api/system_message/read 的 400 / 404 / 200 分支

跑法（项目根目录下）：python -m pytest tests/test_system_message_read_state.py -v
"""

from __future__ import annotations

import logging
import os
import sys
import unittest
from types import SimpleNamespace
from typing import Any

sys.path.append(os.getcwd())

from bson import ObjectId
from fastapi import HTTPException

from db.mongo.mongo_system_message_impl import MongoSystemMessageImpl


class FakeCollection:
    """collection 替身：只实现 update_one 的 $addToSet 语义。"""

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs: list[dict[str, Any]] = docs
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def update_one(self, condition: dict[str, Any], update: dict[str, Any]) -> Any:
        """按 _id 匹配并把 $addToSet 的字段追加进去（不含则新建数组）。"""
        self.calls.append((dict(condition), dict(update)))
        for doc in self.docs:
            if doc.get("_id") == condition.get("_id"):
                for field, value in (update.get("$addToSet") or {}).items():
                    values: list[Any] = doc.setdefault(field, [])
                    if value not in values:
                        values.append(value)
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


def _make_impl(docs: list[dict[str, Any]]) -> tuple[MongoSystemMessageImpl, FakeCollection]:
    """构造一个绕过 __init__（不连库）的 impl，注入 collection 替身。

    :param docs: 初始文档列表
    :return: (impl, collection 替身)
    """
    impl: MongoSystemMessageImpl = MongoSystemMessageImpl.__new__(MongoSystemMessageImpl)
    impl.logger = logging.getLogger("test_system_message")
    collection = FakeCollection(docs)
    impl.table = collection  # type: ignore[assignment]
    return impl, collection


class TestVisibilityAndReadState(unittest.TestCase):
    """可见性与读状态判定。"""

    def test_broadcast_visible_to_everyone(self) -> None:
        """user_ids 为空数组 / 不存在 = 广播，所有用户可见。"""
        self.assertTrue(MongoSystemMessageImpl.is_visible_to({"user_ids": []}, "u1"))
        self.assertTrue(MongoSystemMessageImpl.is_visible_to({}, "u1"))

    def test_targeted_only_visible_to_listed_user(self) -> None:
        """定向消息只对列表内用户可见。"""
        record = {"user_ids": ["u1", "u2"]}
        self.assertTrue(MongoSystemMessageImpl.is_visible_to(record, "u1"))
        self.assertFalse(MongoSystemMessageImpl.is_visible_to(record, "u3"))

    def test_read_state_missing_field_is_unread(self) -> None:
        """历史数据没有 read_user_ids 字段时按未读处理，不能抛异常。"""
        self.assertEqual(MongoSystemMessageImpl.read_state({}, "u1"), (False, 0))
        self.assertEqual(MongoSystemMessageImpl.read_state({"read_user_ids": []}, "u1"), (False, 0))

    def test_read_state_counts_other_users(self) -> None:
        """is_read 只看当前用户，read_count 统计全部已读用户。"""
        record = {"read_user_ids": ["u2", "u3"]}
        self.assertEqual(MongoSystemMessageImpl.read_state(record, "u1"), (False, 2))
        self.assertEqual(MongoSystemMessageImpl.read_state(record, "u2"), (True, 2))


class TestMarkRead(unittest.TestCase):
    """已读写入。"""

    def _doc(self, **extra: Any) -> dict[str, Any]:
        doc: dict[str, Any] = {"_id": ObjectId(), "title": "t", "message": "m", "user_ids": []}
        doc.update(extra)
        return doc

    def test_mark_read_uses_add_to_set(self) -> None:
        """必须用 $addToSet 增量写入，而不是 $set 覆盖整个数组。

        覆盖式写法在并发下会互相丢已读记录（MongoExec.update 只支持 $set，所以本方法
        直接用 collection）。
        """
        doc = self._doc()
        impl, collection = _make_impl([doc])

        self.assertTrue(impl.mark_read(str(doc["_id"]), "u1"))

        _, update = collection.calls[0]
        self.assertEqual(update, {"$addToSet": {"read_user_ids": "u1"}})
        self.assertEqual(doc["read_user_ids"], ["u1"])

    def test_mark_read_is_idempotent(self) -> None:
        """同一用户重复标记不产生重复项，且都返回成功。"""
        doc = self._doc(read_user_ids=["u1"])
        impl, _ = _make_impl([doc])

        self.assertTrue(impl.mark_read(str(doc["_id"]), "u1"))
        self.assertEqual(doc["read_user_ids"], ["u1"])

    def test_mark_read_keeps_existing_users(self) -> None:
        """另一用户已读的记录不能被冲掉。"""
        doc = self._doc(read_user_ids=["u2"])
        impl, _ = _make_impl([doc])

        self.assertTrue(impl.mark_read(str(doc["_id"]), "u1"))
        self.assertEqual(sorted(doc["read_user_ids"]), ["u1", "u2"])

    def test_mark_read_unknown_id_returns_false(self) -> None:
        """ID 不存在时返回 False（matched_count 为 0），不抛异常。"""
        impl, _ = _make_impl([self._doc()])
        self.assertFalse(impl.mark_read(str(ObjectId()), "u1"))

    def test_mark_read_blank_args_returns_false(self) -> None:
        """空 ID / 空用户直接返回 False，不发请求。"""
        impl, collection = _make_impl([self._doc()])
        self.assertFalse(impl.mark_read("", "u1"))
        self.assertFalse(impl.mark_read(str(ObjectId()), ""))
        self.assertEqual(collection.calls, [])

    def test_add_defaults_read_user_ids(self) -> None:
        """新增消息时补上空数组，省得前端判断字段是否存在。"""
        impl, _ = _make_impl([])
        captured: dict[str, Any] = {}

        def _fake_add(table: Any, data: dict[str, Any]) -> tuple[bool, str | None]:
            captured.update(data)
            return True, "fake-id"

        impl.mongo_exec = SimpleNamespace(add=_fake_add)  # type: ignore[assignment]
        ok, _ = impl.add({"title": "t", "message": "m", "create_id": "admin"})

        self.assertTrue(ok)
        self.assertEqual(captured["read_user_ids"], [])


class TestReadEndpoint(unittest.TestCase):
    """POST /api/system_message/read 的分支行为。"""

    def setUp(self) -> None:
        from web_api.routers import system_message as router_module

        self.router_module = router_module
        self.original_impl = router_module.MongoSystemMessageImpl

    def tearDown(self) -> None:
        self.router_module.MongoSystemMessageImpl = self.original_impl

    def _install_stub(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """把路由里的 impl 换成替身。

        :param records: query_by_id 要返回的文档列表
        :return: 记录调用情况的字典
        """
        calls: dict[str, Any] = {"marked": []}
        stub = SimpleNamespace(
            query_by_id=lambda _id: (True, records or None),
            is_visible_to=MongoSystemMessageImpl.is_visible_to,
            mark_read=lambda mid, uid: calls["marked"].append((mid, uid)) or True,
        )
        self.router_module.MongoSystemMessageImpl = lambda: stub  # type: ignore[assignment]
        return calls

    def test_invalid_id_returns_400(self) -> None:
        """非法 ObjectId 应在查库前被拦下，返回 400 而不是 500。"""
        self._install_stub([])
        req = self.router_module.SystemMessageReadRequest(id="not-an-objectid")
        with self.assertRaises(HTTPException) as ctx:
            self.router_module.mark_system_message_read(req, user_id="u1")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_missing_message_returns_404(self) -> None:
        """消息不存在返回 404。"""
        self._install_stub([])
        req = self.router_module.SystemMessageReadRequest(id=str(ObjectId()))
        with self.assertRaises(HTTPException) as ctx:
            self.router_module.mark_system_message_read(req, user_id="u1")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_invisible_message_returns_404_and_does_not_write(self) -> None:
        """定向消息对别人不可见：按 404 处理，且不写入已读。"""
        calls = self._install_stub([{"_id": ObjectId(), "user_ids": ["u9"]}])
        req = self.router_module.SystemMessageReadRequest(id=str(ObjectId()))
        with self.assertRaises(HTTPException) as ctx:
            self.router_module.mark_system_message_read(req, user_id="u1")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(calls["marked"], [])

    def test_broadcast_message_marks_read(self) -> None:
        """广播消息任何人都可标记，返回成功。"""
        calls = self._install_stub([{"_id": ObjectId(), "user_ids": []}])
        message_id = str(ObjectId())
        req = self.router_module.SystemMessageReadRequest(id=message_id)

        resp = self.router_module.mark_system_message_read(req, user_id="u1")

        self.assertEqual(calls["marked"], [(message_id, "u1")])
        self.assertEqual(resp.code, 0)


class TestOpenApiContract(unittest.TestCase):
    """真实导入 FastAPI 应用，核对接口与响应模型。"""

    def test_read_route_and_item_fields(self) -> None:
        """/api/system_message/read 存在，且 SystemMessageItem 暴露 is_read / read_count。"""
        try:
            from web_api.api_run import app
        except ImportError as e:  # pragma: no cover - 依赖缺失时跳过
            self.skipTest(f"依赖缺失，跳过 OpenAPI 校验: {e}")

        schema: dict[str, Any] = app.openapi()
        paths: dict[str, Any] = schema["paths"]
        self.assertIn("/api/system_message/read", paths)
        self.assertIn("post", paths["/api/system_message/read"])

        item: dict[str, Any] = schema["components"]["schemas"]["SystemMessageItem"]
        self.assertIn("is_read", item["properties"])
        self.assertIn("read_count", item["properties"])

        read_req: dict[str, Any] = schema["components"]["schemas"]["SystemMessageReadRequest"]
        self.assertEqual(read_req["required"], ["id"])


if __name__ == "__main__":
    unittest.main()
