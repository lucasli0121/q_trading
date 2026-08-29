#encoding=utf8
'''
Author: liguoqiang
Date: 2022-08-04 16:21:31
LastEditors: liguoqiang
LastEditTime: 2022-08-22 00:36:10
Description: 
'''
 
import time
import prometheus_client
import requests
from prometheus_client import start_http_server, CollectorRegistry, Gauge

gauge = Gauge(name="rank", documentation="人气榜排名", labelnames=["stock_id"], registry=prometheus_client.REGISTRY)

def process_request():
    url = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
    kwargs = {
        "appId": "appId01",
        "pageNo": 1,
        "pageSize": 100,
    }
    try:
        result = requests.post(url, json=kwargs).json()
        data = result.get("data")
        if data is not None:
            for i in result.get("data", []):
                gauge.labels(stock_id=i["sc"]).set(i["rk"])
    except requests.ConnectionError  as err:
        print(err)
    except requests.RequestException as err:
        print(err)
    time.sleep(10)

if __name__ == "__main__":
    start_http_server(8000, registry=prometheus_client.REGISTRY)
    while True:
        process_request()    
