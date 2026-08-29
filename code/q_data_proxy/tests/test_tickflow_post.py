import json
import os
import sys
import unittest

# Ensure the project root is on sys.path when tests are run from the test directory.
sys.path.append(os.getcwd())

from stock_fetch.tickflow_fetch.tickflow_proxy import TickFlowProxy

class TestTickFlowPost(unittest.TestCase):
    def setUp(self) -> None:
        return super().setUp()

    def tearDown(self) -> None:
        return super().tearDown()

    # def test_get_tickflow(self):
    #     tickflow_proxy = TickFlowProxy()
    #     url=tickflow_proxy.server_url + "/quotes"
    #     params = {"symbols": "600000.SH,000001.SZ"}
    #     result, values = tickflow_proxy.get_tickflow(url, params)
    #     if result:
    #         print("GET request successful, response data:")
    #         print(values)
    #     else:
    #         print("GET request failed, error response:")
    #         print(values)
    def test_post_tickflow(self):
        tickflow_proxy = TickFlowProxy()
        url=tickflow_proxy.server_url + "/quotes"
        payload = {"symbols": ["600000.SH", "000001.SZ"]}
        result, values = tickflow_proxy.post_tickflow(url, json.dumps(payload))
        if result:
            print("POST request successful, response data:")
            print(values)
        else:
            print("POST request failed, error response:")
            print(values)
if __name__ == '__main__':
    unittest.main()            