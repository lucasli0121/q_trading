/*
 * @Author: liguoqiang
 * @Date: 2023-02-09 17:20:34
 * @LastEditors: liguoqiang
 * @LastEditTime: 2023-05-16 20:11:07
 * @Description: 定义数据库的开始操作入口，包括通用的操作接口
 */
package mdb

import (
	"fmt"
	"net/http"
	"rhserver/cfg"
	mylog "rhserver/log"
	"rhserver/mdb/common"
	"rhserver/mdb/mongo"
	"rhserver/mdb/mysql"
	"strconv"

	"github.com/gin-gonic/gin"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
)

/**********************************************************************************************
*
* 个股当天实时行情数据函数系列
* 用于实现股票当天实时行情数据包括开盘，收盘，最高，最低等
***********************************************************************************************/

/***********************************************************
* 根据其他条件查询 个股当天实时行情数据
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
* 参数 gin.Context
* 查询条件必须包含code，可能包含行业，开盘，最高，时间范围等
***********************************************************/
func QueryStockRealTimeByCond(c *gin.Context, page *common.PageDao) (int, interface{}) {
	code := c.Query("code")
	if code == "" {
		return http.StatusBadRequest, "code params required"
	}
	startTm := c.Query("start_time")
	endTm := c.Query("end_time")
	if cfg.IsMongo() {
		var me *mongo.StockAHq = mongo.NewStockAHq()
		me.Code = code
		var filter []bson.M
		if startTm != "" {
			filter = append(filter, bson.M{"datetime": bson.M{"$gte": startTm}})
		}
		if endTm != "" {
			filter = append(filter, bson.M{"datetime": bson.M{"$lte": endTm}})
		}
		var wlst []mongo.StockAHq
		filterAll := bson.M{"$and": filter}
		if mongo.QueryStockAHqByCond(code, filterAll, page, bson.M{"datetime": -1}, &wlst) {
			return common.Success, wlst
		}
	} else if cfg.IsMysql() {
		var filter string
		if len(startTm) > 0 {
			filter = fmt.Sprintf("createtime>=datetime('%s')", startTm)
		}
		if len(endTm) > 0 {
			if len(filter) == 0 {
				filter = fmt.Sprintf(" createtime<=datetime('%s')", endTm)
			} else {
				filter += fmt.Sprintf(" and createtime<=datetime('%s')", endTm)
			}
		}
		var wlst []mysql.StockRealTimeData
		if mysql.QueryStockRealTimeDataByCond(code, filter, page, " createtime desc", &wlst) {
			return common.Success, wlst
		}
	}
	return common.DBError, "query failed"
}

/***********************************************************
* 一次查询所有 个股当天实时行情数据，并根据分页返回数据
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
* 参数 gin.Context page
***********************************************************/
func QueryAllLatestRealTimeHq(c *gin.Context, page *common.PageDao) (int, interface{}) {
	if cfg.IsMongo() {
		var wlst []mongo.StockAHq
		mongo.QueryAllStocksHq(page, &wlst)
		return common.Success, wlst
	} else if cfg.IsMysql() {
		var wlst []mysql.StockRealTimeData
		mysql.QueryAllStocksRealTimeData(page, &wlst)
		return common.Success, wlst
	}
	return common.OtherError, "query failed, other error"
}

/***********************************************************
* 添加 个股当天实时行情数据
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
* 参数 gin.Context
***********************************************************/
func InsertStockRealTimeHq(c *gin.Context) (int, interface{}) {
	if cfg.IsMongo() {
		me := mongo.NewStockAHq()
		me.Decode(c)
		if ok := me.Insert(); !ok {
			return common.DBError, "insert error!"
		}
		return common.Success, me
	} else if cfg.IsMysql() {
		me := mysql.NewStockRealTimeData()
		me.DecodeFromGin(c)
		if ok := me.Insert(); !ok {
			return common.DBError, "insert error!"
		}
		return common.Success, me
	}
	return common.OtherError, "insert failed, other error!"
}

/***********************************************************
* 更新 个股当天实时行情数据
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
* 参数 gin.Context
***********************************************************/
func UpdateStockRealTimeHq(c *gin.Context) (int, interface{}) {
	if cfg.IsMongo() {
		me := mongo.NewStockAHq()
		me.Decode(c)
		if me.ID.String() == "" {
			return common.ParamError, "not found id field!"
		}
		if !me.Update() {
			return common.DBError, "update failed!"
		}
		return common.Success, "update ok"
	} else if cfg.IsMysql() {
		me := mysql.NewStockRealTimeData()
		me.DecodeFromGin(c)
		if me.ID <= 0 {
			return common.ParamError, "not found id field!"
		}
		if !me.Update() {
			return common.DBError, "update failed!"
		}
		return common.Success, "update ok"
	}
	return common.OtherError, "update failed, other error"
}

/**********************************************************************************************
*
* 个股历史行情数据函数系列
* 用于实现股票历史行情数据包括开盘，收盘，最高，最低等
* 收集每天收盘后的数据
***********************************************************************************************/

/***********************************************************
* 根据ID查询 个股历史行情数据
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
* 参数 id
***********************************************************/
func QueryStockHisHqById(id string) (int, interface{}) {
	if cfg.IsMongo() {
		var me *mongo.StockHisHq = mongo.NewStockHisHq()
		mId, err := primitive.ObjectIDFromHex(id)
		if err != nil {
			mylog.Log.Errorln(err)
			return common.ParamError, "id format is wrong"
		}
		if me.QueryByID(mId) {
			return common.Success, me
		}
	} else if cfg.IsMysql() {
		me := mysql.NewStockHisHq()
		mId, err := strconv.Atoi(id)
		if err != nil {
			mylog.Log.Errorln(err)
			return common.FormatError, "id format is wrong"
		}
		if me.QueryByID(int64(mId)) {
			return common.Success, me
		}
	}
	return common.OtherError, "other error"
}

/***********************************************************
* 根据其他条件查询 个股历史行情数据
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
* 参数 gin.Contxt page
***********************************************************/
func QueryStockHisHqByCond(c *gin.Context, page *common.PageDao) (int, interface{}) {
	code := c.Query("code")
	if code == "" {
		return common.ParamError, "code params required"
	}
	startDt := c.Query("start_date")
	endDt := c.Query("end_date")
	period := c.Query("period")
	adjust := c.Query("adjust")
	order := c.Query("order")
	if cfg.IsMongo() {
		var filter []bson.M
		if startDt != "" {
			filter = append(filter, bson.M{"date": bson.M{"$gte": startDt}})
		}
		if endDt != "" {
			filter = append(filter, bson.M{"date": bson.M{"$lte": endDt}})
		}
		var wlst []mongo.StockHisHq
		var filterAll = bson.M{}
		if filter != nil {
			filterAll = bson.M{"$and": filter}
		}
		if mongo.QueryStockHisHqByCond(code, filterAll, page, bson.M{"date": -1}, &wlst) {
			return common.Success, wlst
		}
	} else if cfg.IsMysql() {
		filter := "1=1"
		if len(startDt) > 0 {
			filter += fmt.Sprintf(" and createdate>=date('%s')", startDt)
		}
		if len(endDt) > 0 {
			filter += fmt.Sprintf(" and createdate<=date('%s')", endDt)
		}
		filter += fmt.Sprintf(" and %s", mysql.MustNeedQryCond(period, adjust))
		var wlst []mysql.StockHisHq
		var orderStr string
		if order == "" || order == "0" {
			orderStr = " createdate desc"
		} else {
			orderStr = " createdate asc"
		}
		if mysql.QueryStockHisHqByCond(code, filter, page, orderStr, &wlst) {
			return common.Success, wlst
		}
	}
	return common.DBError, "query failed"
}

/***********************************************************
* 添加 个股历史行情数据
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
* 参数 gin.Contxt
***********************************************************/
func InsertStockHisHq(c *gin.Context) (int, interface{}) {
	if cfg.IsMongo() {
		me := mongo.NewStockHisHq()
		me.Decode(c)
		filter := bson.M{"date": me.Date}
		var wlst []mongo.StockHisHq
		if mongo.QueryStockHisHqByCond(me.Code, filter, nil, bson.M{"date": -1}, &wlst) && len(wlst) > 0 {
			me.SetID(wlst[0].ID)
			if me.Update() {
				return common.Success, me
			}
		} else {
			if me.Insert() {
				return common.Success, me
			}
		}
	} else if cfg.IsMysql() {
		me := mysql.NewStockHisHq()
		me.DecodeFromGin(c)
		filter := fmt.Sprintf(" createdate=date('%s') and %s", me.Date, mysql.MustNeedQryCond(me.Period, *me.Adjust))
		var wlst []mysql.StockHisHq
		if mysql.QueryStockHisHqByCond(me.Code, filter, nil, "createdate desc", &wlst) && len(wlst) > 0 {
			me.SetID(wlst[0].ID)
			if me.Update() {
				return common.Success, me
			}
		} else {
			if me.Insert() {
				return common.Success, me
			}
		}
	}
	return common.DBError, "insert error!"
}

/***********************************************************
* 更新 个股历史行情数据
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
* 参数 gin.Contxt
***********************************************************/
func UpdateStockHisHq(c *gin.Context) (int, interface{}) {
	if cfg.IsMongo() {
		me := mongo.NewStockHisHq()
		me.Decode(c)
		if me.ID.String() == "" {
			return common.ParamError, "not found id field!"
		}
		if me.Update() {
			return common.Success, "update ok"
		}
	} else if cfg.IsMysql() {
		me := mysql.NewStockHisHq()
		me.DecodeFromGin(c)
		if me.ID <= 0 {
			return common.ParamError, "not found id field!"
		}
		if me.Update() {
			return common.Success, "update ok"
		}
	}
	return common.DBError, "update failed!"
}
