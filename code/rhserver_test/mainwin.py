'''
Author: liguoqiang
Date: 2022-09-25 12:03:57
LastEditors: liguoqiang
LastEditTime: 2022-10-11 17:24:17
Description: 
'''

# -*- coding: utf-8 -*-
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtGui import *
from PyQt6.QtCore import *
from PyQt6.QtWidgets import *
from PyQt6 import uic
import sys
from views.stocks_view import StocksView
from views.stock_k_view import StockKView

mdiAreaStyle = '''
    QMdiArea
    {
        background:#000000;
        font-size:12px;
        font-family:"Microsoft YaHei";
        color:#FFFFFF;
        background:#000000;
        border: none;
        margin-left:0px;
        padding-left:0px;
    }
'''

def startQApp(stockData):
    app = QApplication(sys.argv)
    mainWnd = MainWindow(Qt.WindowType.Window)
    mainWnd.setStockData(stockData)
    mainWnd.show()
    sys.exit(app.exec())

class MainWindow(QMainWindow):
    def __init__(self, flags: QtCore.Qt.WindowType = ...) -> None:
        super().__init__(None, flags)
        self.mdiArea = QMdiArea()
        self.mdiArea.setBackground(Qt.GlobalColor.transparent)
        self.setCentralWidget(self.mdiArea)
        self.setupUi()

    def setStockData(self, stock):
        self.stockData = stock
        self.mainSubWin.setStockData(stock)

    def setupUi(self):
        self.mdiArea.setObjectName("mdiArea")
        self.mdiArea.setStyleSheet(mdiAreaStyle)
        self.mdiArea.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.mdiArea.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.mdiArea.setViewMode(QMdiArea.ViewMode.TabbedView)
        self.mainSubWin = StocksView(self)
        self.mainSubWin.registerDoubleClickedFunc(self.showStockKView)
        self.mdiArea.addSubWindow(self.mainSubWin)
        self.mainSubWin.setObjectName("mainSubWin")
        _translate = QtCore.QCoreApplication.translate
        #self.mdiArea.setWindowTitle(_translate("mdiArea", "股票策略"))
        self.mainSubWin.setWindowTitle(_translate("mainSubWin", "股票列表"))
        self.mdiArea.subWindowActivated.connect(self.onSubWindowActivate)

    def resizeEvent(self, a0: QtGui.QResizeEvent) -> None:
        super().resizeEvent(a0)
        rc = self.mdiArea.geometry()
        self.mainSubWin.setGeometry(rc)

    def onSubWindowActivate(subWin: QMdiSubWindow):
        pass

    def closeEvent(self, a0: QtGui.QCloseEvent) -> None:
        super().closeEvent(a0)
        self.mdiArea.closeAllSubWindows()
    
    # 显示K线视图
    # code 需要显示的股票代码
    def showStockKView(self, code, name):
        stockView = StockKView(self)
        self.mdiArea.addSubWindow(stockView)
        stockView.showStockK(code, name, self.stockData)