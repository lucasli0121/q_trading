/*
 * @Author: liguoqiang
 * @Date: 2023-02-09 17:20:34
 * @LastEditors: liguoqiang
 * @LastEditTime: 2023-05-16 20:11:07
 * @Description: 定义股票指数接口
 */
package mdb

import (
	"rhserver/cfg"
	mylog "rhserver/log"
	"rhserver/mdb/common"
	"rhserver/mdb/mongo"
	"rhserver/mdb/mysql"

	"github.com/gin-gonic/gin"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
)

/***********************************************************
* 查询股票指数代理接口
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
********************************************************/
func QueryStockIndexById(c *gin.Context) (int, interface{}) {
	id := c.Query("id")
	if id == "" {
		return common.ParamError, "id params required"
	}
	if cfg.IsMongo() {
		var index *mongo.Index = mongo.NewIndex()
		mId, err := primitive.ObjectIDFromHex(id)
		if err != nil {
			mylog.Log.Errorln(err)
			return common.ParamError, "id format is wrong"
		}
		if index.QueryByID(mId) {
			return common.Success, index
		} else {
			return common.FormatError, "encoding json failed"
		}
	}
	return common.OtherError, "other error"
}

/***********************************************************
* 根据code 查询股票指数接口
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
***********************************************************/
func QueryStockIndexByCode(c *gin.Context) (int, interface{}) {
	code := c.Query("code")
	if code == "" {
		return common.ParamError, "code params required"
	}
	if cfg.IsMongo() {
		var filter bson.M
		if code != "" {
			filter = bson.M{"code": code}
		}
		var wlst []mongo.Index
		if mongo.QueryIndexByCond(filter, &wlst) {
			return common.Success, wlst
		} else {
			return common.DBError, "query failed"
		}
	}
	return common.OtherError, "other error"
}

/***********************************************************
* func QueryAllIndexInfo()
* If cfg.IsMysql() then call mysql.QueryAllIndexInfo() in mysql_stocka.go
********************************************************/
func QueryAllIndexInfo(c *gin.Context) (int, interface{}) {
	if cfg.IsMysql() {
		var gList []mysql.IndexInfo
		mysql.QueryAllIndexInfo(&gList)
		return common.Success, gList
	}
	return common.OtherError, "query failed!"
}

/***********************************************************
* 添加股票指数接口
***********************************************************/
func InsertStockIndex(c *gin.Context) (int, interface{}) {
	if cfg.IsMongo() {
		index := mongo.NewIndex()
		index.Decode(c)
		var gList []mongo.Index
		mongo.QueryIndexByCond(bson.M{"code": index.Code}, &gList)
		var ok bool = false
		if len(gList) > 0 {
			for _, v := range gList {
				index.ID = v.ID
				ok = index.Update()
			}
		} else {
			ok = index.Insert()
		}
		if !ok {
			return common.DBError, "insert error!"
		}
		return common.Success, index
	}
	return common.OtherError, "insert error!"
}

/***********************************************************
* 更新 股票指数接口
***********************************************************/
func UpdateStockIndex(c *gin.Context) (int, interface{}) {
	if cfg.IsMongo() {
		index := mongo.NewIndex()
		index.Decode(c)
		if index.ID.String() == "" {
			return common.ParamError, "not found id field!"
		}
		if !index.Update() {
			return common.DBError, "update failed!"
		}
		return common.Success, "update ok"
	}
	return common.OtherError, "insert failed!"
}

/***********************************************************
* func QueryAllIndexCoStock()
* If cfg.IsMysql() then call mysql.QueryAllCoStock() in mysql_stocka.go
********************************************************/
func QueryAllIndexCoStock(c *gin.Context) (int, interface{}) {
	if cfg.IsMysql() {
		var gList []mysql.IndexStock
		mysql.QueryAllIndexCoStock(&gList)
		return common.Success, gList
	}
	return common.OtherError, "query failed!"
}

/**
 * @function: QueryIndexCoStockByCond
 * @description: query co-stock by condition, condition include index code, publish date, etc.
 * @return {*}
 */
func QueryIndexCoStockByCond(c *gin.Context) (int, interface{}) {
	indexCode := c.Query("index_code")
	publicDate := c.Query("public_date")
	if cfg.IsMysql() {
		var filter string
		if indexCode != "" {
			filter = "index_code='" + indexCode + "'"
		}
		if publicDate != "" {
			if filter != "" {
				filter += " and "
			}
			filter += "public_date='" + publicDate + "'"
		}
		var gList []mysql.IndexStock
		if mysql.QueryIndexCoStockByCond(filter, "code", &gList) {
			return common.Success, gList
		}
	}
	return common.OtherError, "query failed!"
}

/**********************************************************************************************
*
* 股票指数行情表操作函数系列
* 用于实现股票详细的行情数据包括开盘，收盘，最高，最低等
*
***********************************************************************************************/

/***********************************************************
* 查询 股票指数行情接口
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
* 参数 code
***********************************************************/
func QueryStockIndexHqByCode(c *gin.Context) (int, interface{}) {
	code := c.Query("code")
	if code == "" {
		return common.ParamError, "code params required"
	}
	if cfg.IsMongo() {
		var filter bson.M
		if code != "" {
			filter = bson.M{"code": code}
		}
		var wlst []mongo.IndexHq
		if mongo.QueryIndexHqByCond(filter, &wlst) {
			return common.Success, wlst
		} else {
			return common.DBError, "query failed"
		}
	}
	return common.OtherError, "query failed!"
}

/***********************************************************
* 添加 股票指数行情接口
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
* 参数 gin.Context
***********************************************************/
func InsertStockIndexHq(c *gin.Context) (int, interface{}) {
	if cfg.IsMongo() {
		me := mongo.NewIndexHq()
		me.Decode(c)
		var gList []mongo.IndexHq
		mongo.QueryIndexHqByCond(bson.M{"code": me.Code, "date": me.Date}, &gList)
		var ok bool = false
		if len(gList) > 0 {
			for _, v := range gList {
				me.ID = v.ID
				ok = me.Update()
			}
		} else {
			ok = me.Insert()
		}
		if !ok {
			return common.DBError, "insert error!"
		}
		return common.Success, me
	}
	return common.OtherError, "insert failed!"
}

/***********************************************************
* 更新 股票指数行情接口
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
* 参数 gin.Context
***********************************************************/
func UpdateStockIndexHq(c *gin.Context) (int, interface{}) {
	if cfg.IsMongo() {
		me := mongo.NewIndexHq()
		me.Decode(c)
		if me.ID.String() == "" {
			return common.ParamError, "not found id field!"
		}
		if !me.Update() {
			return common.DBError, "update failed!"
		}
		return common.Success, "update ok"
	} else if cfg.IsMysql() {
		// me := mysql.NewIndexHq()
		// me.Decode(c)
		// if me.ID == 0 {
		// 	return http.StatusAccepted, "not found id field!"
		// }
		// if !me.Update() {
		// 	return http.StatusAccepted, "update failed!"
		// }
		return common.Success, "update ok"
	}
	return common.OtherError, "update failed!"
}
