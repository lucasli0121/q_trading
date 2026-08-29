import unittest
from unittest.mock import MagicMock

from api.order import OrderApi


class TestOrderApi(unittest.TestCase):
    def test_create_and_list_and_update_status_use_swagger_contract(self) -> None:
        client = MagicMock()
        api = OrderApi(client)

        result = api.create(
            user_strategy_id="us-1",
            stock_code="600000",
            entrust_quantity=100,
            trade_price=10.5,
            trade_quantity=100,
            status="委托",
            time="2026-07-02 09:00:00",
            position_price=10.8,
            profit_rate=0.03,
            profit_amount=300.0,
            commission_fee=5.0,
            action="买入",
        )
        client.post.assert_any_call(
            "/api/order/create",
            {
                "user_strategy_id": "us-1",
                "stock_code": "600000",
                "entrust_quantity": 100,
                "trade_price": 10.5,
                "trade_quantity": 100,
                "position_price": 10.8,
                "profit_rate": 0.03,
                "profit_amount": 300.0,
                "commission_fee": 5.0,
                "status": "委托",
                "create_time": "2026-07-02 09:00:00",
                "action": "买入",
            },
        )
        self.assertEqual(result["position_price"], 10.8)
        self.assertEqual(result["profit_rate"], 0.03)
        self.assertEqual(result["profit_amount"], 300.0)
        self.assertEqual(result["commission_fee"], 5.0)
        self.assertEqual(result["action"], "买入")

        api.list("us-1")
        client.get.assert_any_call("/api/order/list/us-1", None)

        api.list_by_user()
        client.get.assert_any_call("/api/order/user/list", None)

        api.list_by_user(
            start_time="2026-07-01 00:00:00",
            end_time="2026-07-02 00:00:00",
            status="成功",
            action="买入",
        )
        client.get.assert_any_call(
            "/api/order/user/list",
            {
                "start_time": "2026-07-01 00:00:00",
                "end_time": "2026-07-02 00:00:00",
                "status": "成功",
                "action": "买入",
            },
        )

        api.get("o-1")
        client.get.assert_any_call("/api/order/o-1")

        api.update_status("o-1", "成交")
        client.put.assert_any_call("/api/order/o-1/status", {"status": "成交"})

        api.cancel("o-1")
        client.put.assert_any_call("/api/order/o-1/status", {"status": "撤单"})


if __name__ == "__main__":
    unittest.main()
