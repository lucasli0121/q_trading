'''
Author: liguoqiang
Date: 2022-08-27 11:45:12
LastEditors: liguoqiang
LastEditTime: 2023-02-18 09:36:05
Description: 实现一个简单控制唯一元素的队列，这个队列不能保证完全唯一，可以保证依然在缓存队列中的元素唯一
'''
# encoding="utf8"

import queue
import threading

class UniQueue(queue.Queue):
    def __init__(self, maxsize=2000) -> None:
        super().__init__(maxsize)
        # 用于保证“查重 → 入队”的原子性
        self._put_lock = threading.Lock()
    
    def put(self, item, block=True, timeout=None):
        with self._put_lock:
            if item in self.queue:
                return
            super().put(item, block=block, timeout=timeout)

