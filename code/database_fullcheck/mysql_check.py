'''
Author: liguoqiang
Date: 2023-03-25 16:53:23
LastEditors: liguoqiang
LastEditTime: 2023-03-27 20:37:53
Description: 
'''
# coding="utf8"

import datetime
import math
import MySQLdb as SqlDb

mysqlHost = "192.168.1.63"
mysqlPort = 9306
mysqlUser = "rh"
mysqlPasswd = "rh123"
mysqlDatabase = "rh"

global db
db = None

stockInfoTbl = "stock_info_tbl"

def connectSql() -> bool:
    ret = False
    try:
        global db
        db = SqlDb.connect(host = mysqlHost, port=mysqlPort, user=mysqlUser, password=mysqlPasswd, database=mysqlDatabase, charset="utf8")
        ret = True
    except Exception as err:
        print(err)
        ret = False
    return ret


def stockRealHqTbl(code) -> str:
    tblName = "stock_hq_" + code
    return tblName

def stockHisTbl(code) -> str:
    tblName = "stock_his_" + code
    return tblName

##
# 从数据库查询stock_info_tbl表，获取所有股票信息
#
def checkAllStockHisHqData():
    sql = "select * from " + stockHisTbl
    try:
        cur = db.cursor(SqlDb.cursors.DictCursor)
        cur.execute(sql)
        res = cur.fetchall()
        if res is not None:
            for stock in res:
                checkOneStockHisHqData(stock)
        cur.close()
    except Exception as err:
        print(err)
    
# 检查单个历史行情表数据是否缺失
def checkOneStockHisHqData(stock):
    code = stock["code"]
    tblName = stockHisTbl(code)
    sql = "select * from " + tblName + " order by createdate desc"
    today = datetime.now().date().strftime("%Y-%m-%d")
    try:
        cur = db.cursor(SqlDb.cursors.DictCursor)
        cur.execute(sql)
        res = cur.fetchall()
        if res is not None:
            for data in res:
                data["createdate"]
    except Exception as err:
        print(err)

if connectSql():
    checkAllStockHisHqData()
    db.close()