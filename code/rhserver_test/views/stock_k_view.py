'''
Author: liguoqiang
Date: 2022-10-11 10:01:14
LastEditors: liguoqiang
LastEditTime: 2022-10-27 19:31:38
Description: 
'''

# -*- coding: utf-8 -*-
import logging
import threading
import time
import pandas as pd
import mplfinance as mpl
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtGui import *
from PyQt6.QtCore import *
from PyQt6.QtWidgets import *
import talib as ta

from data.stock_data import StockData

viewStyle = '''

    QWidget
    {
        background:#000000;
        border:none;
        text-align: center;
        font-size: 11px;
        font-family: "Microsoft YaHei";
        color: #ffffff;
    }
'''

class StockKView(QWidget):
    _threadStart = False
    _stockData: StockData = None
    _code = None
    _name = None
    _hisDayHq = None
    
    def __init__(self, parent = None) -> None:
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self._translate = QCoreApplication.translate
        self.setupUi()
        self._threadSem = QSemaphore(1)
        self._lock = QMutex()
        self._thread = threading.Thread(target=self.threadRun, args=(), kwargs=None)
    
    def setupUi(self):
        self.setStyleSheet(viewStyle)

    def closeEvent(self, a0: QtGui.QCloseEvent) -> None:
        try:
            if self._thread is not None:
                self._threadStart = False
                self._thread.join(2000)
            if self._timer is not None:
                self._timer.stop()
        except Exception as err:
            self._logger.error(err)
        super().closeEvent(a0)

    def showStockK(self, code, name, stockData):
        self._stockData = stockData
        self._code = code
        self._name = name
        title = code + ": " + name
        self.setWindowTitle(title)
        self._thread.start()
        self._timer = QTimer(self)
        # self._timer.timeout.connect(self.onTimerOut)
        # self._timer.start(3000)
        self.queryStockHisHq()

    def threadRun(self, *args, **kwargs):
        pass
        # while self._threadStart:
        #     if self._threadSem.tryAcquire(1):
        #         self.queryStockHisHq()
        #     time.sleep(0.5)

    # 定时器运行的方法
    def onTimerOut(self):
        self.loadStocksData()

    def loadStocksData(self):
        self._lock.lock()
        if self._threadSem.available() == 0:
            self._threadSem.release(1)
        self._lock.unlock()

    # 查询股票对应历史数据以及当前数据
    def queryStockHisHq(self):
        res = self._stockData.getStockHisHq(self._code)
        if res is not None and res["code"] == 200:
            self._hisDayHq = res["data"]
            if self._hisDayHq is None:
                return
            self.handleDayK()

    # 处理日K线数据
    def handleDayK(self):
        df = self._stockData.jsonHisHqToDf(self._hisDayHq, "date", "date")
        close = df["close"]
        print(close.values)
        diff, dea, macd = ta.MACD(close.values, fastperiod = 12, slowperiod = 26, signalperiod = 9)
        print(diff, dea, macd)
        mpl.plot(df, type="candle", style="yahoo")

