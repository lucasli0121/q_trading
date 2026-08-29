'''
Author: liguoqiang
Date: 2023-02-20 20:15:37
LastEditors: liguoqiang
LastEditTime: 2023-03-25 17:34:35
Description: 把之前mongo数据库中已经存储的数据同步到mysql中
'''
# coding="utf8"

import math
import MySQLdb as SqlDb
from pymongo.collection import Collection
import pymongo
import bson
from pymongo import MongoClient

mysqlHost = "192.168.1.63"
mysqlPort = 9306
mysqlUser = "rh"
mysqlPasswd = "rh123"
mysqlDatabase = "rh"

mongoHost = "192.168.1.63"
mongoPort = 27017
mongoUser = "rh"
mongoPasswd = "rh@123"
mongoDatabase = "rh"

global mysqlDb
mysqlDb = None
global mongoDb
mongoDb = None

stockInfoTbl = "stock_info_tbl"

def mysqlConnect() -> bool:
    ret = False
    try:
        global mysqlDb
        mysqlDb = SqlDb.connect(host = mysqlHost, port=mysqlPort, user=mysqlUser, password=mysqlPasswd, database=mysqlDatabase, charset="utf8")
        ret = True
    except Exception as err:
        print(err)
        ret = False
    return ret

def mongoConnect():
    ret = False
    try:
        global mongoDb
        conn = MongoClient(host = mongoHost, port=mongoPort, username=mongoUser, password=mongoPasswd, authSource=mongoDatabase)
        mongoDb = conn.get_database(mongoDatabase)
        ret = True
    except Exception as err:
        print(err)
        ret = False
    return ret

def mongoStockRealHqTbl(code) -> Collection:
    return mongoDb["stock_hq_" + code]

def mongoStockHisHqTbl(code) -> Collection:
    return mongoDb["stock_his_" + code]
    
def mysqlStockRealHqTbl(code) -> str:
    tblName = "stock_hq_" + code
    if not mysqlTableExist(tblName):
        mysqlCreateRealHqTbl(tblName)
    return tblName

def mysqlStockHisTbl(code) -> str:
    tblName = "stock_his_" + code
    if not mysqlTableExist(tblName):
        mysqlCreateHisHqTbl(tblName)
    return tblName

def mysqlTableExist(tblName):
    res = False
    try:
        cur = mysqlDb.cursor()
        cur.execute("show tables")
        tables = cur.fetchall()
        for tbl in tables:
            if tbl[0] == tblName:
                res = True
                break
    except Exception as err:
        print(err)
    return res
    
def mysqlCreateRealHqTbl(tblName):
    sql = "create table " + tblName + """ (
        id MEDIUMINT NOT NULL AUTO_INCREMENT,
        code char(32) NOT NULL COMMENT '代码',
        name varchar(32) NOT NULL COMMENT '名称',
        price float comment '当前价',
        pchg float comment '涨跌幅',
        chgamount float comment '涨跌额',
        volume float comment '成交量',
        amount float comment '成交额',
        high float comment '最高价',
        low float comment '最低价',
        open float comment '开盘价',
        preclose float comment '前日收盘价',
        turnover float comment '换手率',
        pe float comment '动态市盈率',
        cap float comment '市值',
        fcap float comment '流通市值',
        chginyear float comment '年度最大涨幅',
        createtime datetime comment '新增日期',
        PRIMARY KEY (`id`,`createtime`)
    )"""
    try:
        cur = mysqlDb.cursor()
        cur.execute(sql)
        mysqlDb.commit()
    except Exception as err:
        mysqlDb.rollback()
        print(err)

def mysqlCreateHisHqTbl(tblName):
    sql = "create table " + tblName + """ (
        id MEDIUMINT NOT NULL AUTO_INCREMENT,
        code char(32) NOT NULL COMMENT '代码',
        name varchar(32) NOT NULL COMMENT '名称',
        open float comment '开盘价',
        close float comment '收盘价',
        high float comment '最高价',
        low float comment '最低价',
        volume float comment '成交量',
        amount float comment '成交额',
        pchg float comment '涨跌幅',
        chgamount float comment '涨跌额',
        turnover float comment '换手率',
        adjust char(16) comment '复权 前复权(qfq) 后复权(hfq) 除权(空)',
        createdate date comment '收盘日期',
        PRIMARY KEY (`id`,`createdate`)
    )"""
    try:
        cur = mysqlDb.cursor()
        cur.execute(sql)
        mysqlDb.commit()
    except Exception as err:
        mysqlDb.rollback()
        print(err)

#
# 查询mongo所有股票基本信息数据
#
def queryAllStockInfoFromMongo():
    return mongoDb[stockInfoTbl].find()

# 
# 根据mongo中股票代码查询对应表的实时行情数据
#
def queryStockRealHqFromMongo(code):
    return mongoStockRealHqTbl(code).find()

# 
# 根据mongo中股票代码查询对应表的历史行情数据
#
def queryStockHisHqFromMongo(code):
    return mongoStockHisHqTbl(code).find()
#
# 同步股票基本信息表
#
def syncStockInfoTbl(stockInfoCursor):
    if stockInfoCursor is None:
        return
    try:
        for obj in stockInfoCursor:
            code = obj["code"]
            # 先查找MYSQL数据库中是否有相同的code股票, 如果有相同的股票就不再处理
            sql = "select * from " + stockInfoTbl + " where code='" + code + "'"
            cur = mysqlDb.cursor(SqlDb.cursors.DictCursor)
            cur.execute(sql)
            res = cur.fetchone()
            cur.close()
            if res is None or len(res) == 0:
                keys = None
                values = None
                del obj["_id"]
                keys = ",".join(obj.keys())
                for item in obj.items():
                    val = item[1]
                    if val is None:
                        val = "NULL"
                    elif type(val) is str:
                        val = "'" + val + "'"
                    else:
                        val = str(val)
                    if values is None:
                        values = val
                    else:
                        values = values + "," + val
                try:
                    sql = "insert into " + stockInfoTbl + " (" + keys + ") values (" + values + ")"
                    cur = mysqlDb.cursor()
                    cur.execute(sql)
                    mysqlDb.commit()
                    cur.close()
                except Exception as err:
                    mysqlDb.rollback()
                    print(err)
    except Exception as err:
        print(err)

#
# 同步股票实时行情表
#
def syncStockRealHq(stockInfoCursor):
    if stockInfoCursor is None:
        return
    try:
        for obj in stockInfoCursor:
            code = obj["code"]
            # mysql中对应数据表名称
            mysqlTblName = mysqlStockRealHqTbl(code)
            #查询mongo中股票代码对应表的实时行情数据
            stockCursor = queryStockRealHqFromMongo(code)
            for stock in stockCursor:
                dt = stock["datetime"]
                # 先查找MYSQL数据库中是否有相同的行情数据, 如果没有相同的股票就增加，查询条件是根据时间查询
                sql = "select * from " + mysqlTblName + " where createtime='" + dt + "'"
                cur = mysqlDb.cursor(SqlDb.cursors.DictCursor)
                cur.execute(sql)
                res = cur.fetchone()
                cur.close()
                if res is None or len(res) == 0:
                    del stock["_id"]
                    del stock["datetime"]
                    stock["createtime"] = dt
                    keys = ",".join(stock.keys())
                    values = None
                    for item in stock.items():
                        val = item[1]
                        if type(val) is str:
                            val = "'" + val + "'"
                        elif math.isnan(val):
                            val = "NULL"
                        else:
                            val = str(val)
                        if values is None:
                            values = val
                        else:
                            values = values + "," + val
                    try:
                        sql="insert into " + mysqlTblName + " (" + keys + ") values (" + values + ")"
                        cur = mysqlDb.cursor()
                        cur.execute(sql)
                        mysqlDb.commit()
                        cur.close()
                    except Exception as err:
                        print(err)
                        mysqlDb.rollback()
    except Exception as err:
        print(err)


#
# 同步股票历史行情表
#
def syncStockHisHq(stockInfoCursor):
    if stockInfoCursor is None:
        return
    try:
        for obj in stockInfoCursor:
            code = obj["code"]
            # mysql中对应历史数据表名称
            mysqlTblName = mysqlStockHisTbl(code)
            #查询mongo中股票代码对应表的历史行情数据
            stockCursor = queryStockHisHqFromMongo(code)
            for stock in stockCursor:
                dt = stock["date"]
                del stock["date"]
                stock["createdate"] = dt
                # 先查找MYSQL数据库中是否有相同的行情数据, 如果没有相同的股票就增加，查询条件是根据时间查询
                sql = "select * from " + mysqlTblName + " where createdate='" + dt + "'"
                cur = mysqlDb.cursor(SqlDb.cursors.DictCursor)
                cur.execute(sql)
                res = cur.fetchone()
                cur.close()
                if res is None or len(res) == 0:
                    del stock["_id"]
                    keys = ",".join(stock.keys())
                    values = None
                    for item in stock.items():
                        val = item[1]
                        if type(val) is str:
                            val = "'" + val + "'"
                        elif math.isnan(val):
                            val = "NULL"
                        else:
                            val = str(val)
                        if values is None:
                            values = val
                        else:
                            values = values + "," + val
                    try:
                        sql="insert into " + mysqlTblName + " (" + keys + ") values (" + values + ")"
                        cur = mysqlDb.cursor()
                        cur.execute(sql)
                        mysqlDb.commit()
                        cur.close()
                    except Exception as err:
                        print(err)
                        mysqlDb.rollback()
    except Exception as err:
        print(err)

if mysqlConnect() and mongoConnect():
    resCursor = queryAllStockInfoFromMongo()
    #syncStockInfoTbl(resCursor)
    #syncStockRealHq(resCursor)
    syncStockHisHq(resCursor)
    resCursor.close()
    mysqlDb.close()