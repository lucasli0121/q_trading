'''
Author: liguoqiang
Date: 2022-09-29 18:11:50
LastEditors: liguoqiang
LastEditTime: 2022-10-11 12:07:07
Description: 
'''

# -*- coding: utf-8 -*-

from concurrent.futures import thread
from operator import mod
from statistics import mode
import string
from sys import flags
import typing
import logging
import threading
import time
from PyQt6.QtWidgets import *
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtGui import *
from PyQt6.QtCore import *
from data.stock_data import StockData


tableViewStyle = '''
    QHeaderView
    {
        background:#000000;
    }

    QHeaderView::section
    {
        font-size:12px;
        font-family:"Microsoft YaHei";
        color:#FFFFFF;
        background:#000000;
        border: none;
        text-align:left;
        min-width:40px;
        min-height:40px;
        max-height:40px;
        margin-left:0px;
        padding-left:0px;
    }

    QTableView
    {
        background:#000000;
        border:none;
        text-align: center;
        font-size: 11px;
        font-family: "Microsoft YaHei";
        color: #ffffff;
    }
    QTableView::item
    {
        border-bottom:1px solid #EEF1F7 ;
        margin-left:0px;
        padding-left:0px;
    }

    QTableView::item::selected
    {
        background:grey;
    }

    QScrollBar::handle:vertical
    {
        background: rgba(255,255,255,20%);
        border: 0px solid grey;
        border-radius:3px;
        width: 8px;
    }

    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical
    {
        background:rgba(255,255,255,10%);
    }


    QScollBar::add-line:vertical, QScrollBar::sub-line:vertical
    {
        background:transparent;
    }
'''

class StocksView(QTableView):
    _stockData = None
    _pageNo = 1
    _pageSize = 20
    _totalPages = 0
    _threadStart = False
    
    def __init__(self, parent = None) -> None:
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self.setupUi()
        self._threadSem = QSemaphore(1)
        self._lock = QMutex()
        self._thread = threading.Thread(target=self.threadRun, args=(), kwargs=None)

    # 初始化表格控件界面    
    def setupUi(self):
        self.setStyleSheet(tableViewStyle)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        labels = ["代码", "名称", "当前", "开盘", "昨收", "最高", "最低", "涨幅%", "涨跌", "成交", "换手%", "市盈%", "年%", "时间"]
        model = QStandardItemModel()
        model.setHorizontalHeaderLabels(labels)
        self.setModel(model)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)  # 整行选择
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers) # 禁止编辑
        self.doubleClicked.connect(self.onDoubleClicked) # 连接表格鼠标双击事件

    def registerDoubleClickedFunc(self, func):
        self._doubleClickedFunc = func

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

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:
        super().resizeEvent(e)
        sz = e.size()
        rc = self.geometry()
        
    def setStockData(self, stockData):
        self._stockData = stockData
        self._threadStart = True
        self._thread.start()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.onTimerOut)
        self._timer.start(3000)
        
    # 定时器运行的方法
    def onTimerOut(self):
        self.loadStocksData()

    def threadRun(self, *args, **kwargs):
        while self._threadStart:
            if self._threadSem.tryAcquire(1):
                self.queryStocksHq()
            time.sleep(0.5)

    def queryStocksHq(self):
        res = self._stockData.queryAllStocksHq(self._pageNo, self._pageSize)
        if res is not None and res["code"] == 200:
            pageNo = res["pageNo"]
            pageSize = res["pageSize"]
            if pageNo == 1:
                self._totalPages = res["totalPage"]
            stocks = res["data"]
            if stocks is None:
                return
            self.handleStocks(stocks)

    def handleStocks(self, stocks):
        if stocks is None:
            return
        for v in stocks:
            self.updateItemStock(v)

    def updateItemStock(self, stock):
        try:
            model = self.model()
            code = stock["code"]
            name = stock["name"]
            price = stock["price"]
            open = stock["open"]
            preclose = stock["preclose"]
            high = stock["high"]
            low = stock["low"]
            pchg = stock["pchg"]
            chgamount = stock["chgamount"]
            volume = stock["volume"]
            turnover = stock["turnover"]
            pe = stock["pe"]
            chginyear = stock["chginyear"]
            dt = stock["datetime"]
            finditem = False
            for row in range(model.rowCount()):
                d = model.item(row, 0).text()
                if d == code:
                    model.setItem(row, 0, QStandardItem(code))
                    model.setItem(row, 1, QStandardItem(name))
                    model.setItem(row, 2, QStandardItem(str(price)))
                    model.setItem(row, 3, QStandardItem(str(open)))
                    model.setItem(row, 4, QStandardItem(str(preclose)))
                    model.setItem(row, 5, QStandardItem(str(high)))
                    model.setItem(row, 6, QStandardItem(str(low)))
                    item = QStandardItem(str(pchg))
                    item.setForeground(Qt.GlobalColor.red if float(pchg) >= 0.0 else Qt.GlobalColor.green)
                    model.setItem(row, 7, item)
                    item = QStandardItem(str(chgamount))
                    item.setForeground(Qt.GlobalColor.red if float(chgamount) >= 0.0 else Qt.GlobalColor.green)
                    model.setItem(row, 8, item)
                    model.setItem(row, 9, QStandardItem(str(volume)))
                    model.setItem(row, 10, QStandardItem(str(turnover)))
                    model.setItem(row, 11, QStandardItem(str(pe)))
                    item = QStandardItem(str(chginyear))
                    item.setForeground(Qt.GlobalColor.red if float(chginyear) >= 0.0 else Qt.GlobalColor.green)
                    model.setItem(row, 12, item)
                    model.setItem(row, 13, QStandardItem(str(dt)))
                    finditem = True
                    break
            if finditem is False:
                items = []
                items.append(QStandardItem(code))
                items.append(QStandardItem(name))
                items.append(QStandardItem(str(price)))
                items.append(QStandardItem(str(open)))
                items.append(QStandardItem(str(preclose)))
                items.append(QStandardItem(str(high)))
                items.append(QStandardItem(str(low)))
                item = QStandardItem(str(pchg))
                item.setForeground(Qt.GlobalColor.red if float(pchg) >= 0.0 else Qt.GlobalColor.green)
                items.append(item)
                item = QStandardItem(str(chgamount))
                item.setForeground(Qt.GlobalColor.red if float(chgamount) >= 0.0 else Qt.GlobalColor.green)
                items.append(item)
                items.append(QStandardItem(str(volume)))
                items.append(QStandardItem(str(turnover)))
                items.append(QStandardItem(str(pe)))
                item = QStandardItem(str(chginyear))
                item.setForeground(Qt.GlobalColor.red if float(chginyear) >= 0.0 else Qt.GlobalColor.green)
                items.append(item)
                items.append(QStandardItem(str(dt)))
                model.appendRow(items)
            self.setModel(model)
        except Exception as err:
            self._logger.error(err)
            
    def selectionChanged(self, selected: QtCore.QItemSelection, deselected: QtCore.QItemSelection) -> None:
        super().selectionChanged(selected, deselected)

    # 双击表格事件
    def onDoubleClicked(self, index: QModelIndex):
        model = index.model()
        row = index.row()
        code = model.item(row, 0).text()
        name = model.item(row, 1).text()
        self._doubleClickedFunc(code, name) if self._doubleClickedFunc is not None else ...


    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        key = e.key()
        modify = e.modifiers()
        if modify == Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_PageUp:
                self.loadPageUp()
            elif key == Qt.Key.Key_PageDown:
                self.loadPageDown()
        super().keyPressEvent(e)

    def loadPageUp(self):
        if self._pageNo > 1:
            self._pageNo = self._pageNo - 1
            self.clearModelItems()
            self.loadStocksData()
            
    def loadPageDown(self):
        if self._pageNo < self._totalPages:
            self._pageNo = self._pageNo + 1
            self.clearModelItems()
            self.loadStocksData()
            
    def clearModelItems(self):
        model: QStandardItemModel = self.model()
        model.removeRows(0, model.rowCount())
        self.setModel(model)
        
    def loadStocksData(self):
        self._lock.lock()
        if self._threadSem.available() == 0:
            self._threadSem.release(1)
        self._lock.unlock()