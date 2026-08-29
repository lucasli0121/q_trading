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
* 个股基本信息数据函数系列
* 基本信息包括：代码，名称，所属行业，交易量，股本，估值等数据
* 用于实现股票详细的行情数据包括开盘，收盘，最高，最低等
***********************************************************************************************/
/***********************************************************
* 根据ID查询 个股基本数据
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
* 参数 id
***********************************************************/
func QueryStockInfoById(c *gin.Context) (int, interface{}) {
	id := c.Query("id")
	if id == "" {
		return common.ParamError, "id params required"
	}
	if cfg.IsMongo() {
		mId, err := primitive.ObjectIDFromHex(id)
		if err != nil {
			mylog.Log.Errorln(err)
			return common.FormatError, "id format is wrong"
		}
		var me *mongo.StockInfo = mongo.NewStockInfo()
		if me.QueryByID(mId) {
			return common.Success, me
		} else {
			return common.FormatError, "encoding json failed"
		}
	} else if cfg.IsMysql() {
		var me *mysql.StockInfo = mysql.NewStockInfo()
		mid, err := strconv.Atoi(id)
		if err != nil {
			mylog.Log.Errorln(err)
			return common.FormatError, "id format is wrong"
		}
		if me.QueryByID(int64(mid)) {
			return common.Success, me
		} else {
			return common.FormatError, "encoding json failed"
		}
	}
	return common.OtherError, "query stockinfo by id failed"
}

/***********************************************************
* 根据其他条件查询 个股基本数据
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
* 参数 code name industry 等
***********************************************************/
func QueryStockInfoByCond(c *gin.Context, page *common.PageDao) (int, interface{}) {
	code := c.Query("code")
	industry := c.Query("industry")
	if cfg.IsMongo() {
		var filter []bson.M
		if code != "" {
			filter = append(filter, bson.M{"code": code})
		}
		if industry != "" {
			filter = append(filter, bson.M{"industry": industry})
		}
		var wlst []mongo.StockInfo
		filterAll := bson.M{}
		if filter != nil {
			filterAll = bson.M{"$and": filter}
		}
		// 分页查询, 根据his_update_date排序
		if mongo.QueryStockInfoByCond(filterAll, page, bson.M{"his_update_date": -1}, &wlst) {
			return common.Success, wlst
		}
	} else if cfg.IsMysql() {
		var wlst []mysql.StockInfo
		var filter string
		if code != "" {
			filter = fmt.Sprintf("code = '%s'", code)
		}
		if industry != "" {
			if len(filter) > 0 {
				filter += " and "
			}
			filter += fmt.Sprintf("industry like '%s'", industry)
		}
		if mysql.QueryStockInfoByCond(filter, page, "code", &wlst) {
			return common.Success, wlst
		}
	}
	return common.OtherError, "query failed"
}

/***********************************************************
* 添加 个股基本数据
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
* 参数 gin.Context
***********************************************************/
func InsertStockInfo(c *gin.Context) (int, interface{}) {
	var ok bool = false
	if cfg.IsMongo() {
		me := mongo.NewStockInfo()
		me.Decode(c)
		var gList []mongo.StockInfo
		mongo.QueryStockInfoByCond(bson.M{"code": me.Code}, nil, bson.M{"his_update_date": 1}, &gList)
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
	} else if cfg.IsMysql() {
		me := mysql.NewStockInfo()
		me.DecodeFromGin(c)
		var gList []mysql.StockInfo
		filter := fmt.Sprintf("code='%s'", me.Code)
		mysql.QueryStockInfoByCond(filter, nil, nil, &gList)
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
	return common.OtherError, "other error!"
}

/***********************************************************
* 更改 个股基本数据
* 首先判断系统对接哪种数据库，然后把操作转发给对应执行体
* 参数 gin.Context
***********************************************************/
func UpdateStockInfo(c *gin.Context) (int, interface{}) {
	if cfg.IsMongo() {
		me := mongo.NewStockInfo()
		me.Decode(c)
		if me.ID.String() == "" {
			return common.ParamError, "not found id field!"
		}
		if !me.Update() {
			return common.DBError, "update failed!"
		}
		return common.Success, "update ok"
	} else if cfg.IsMysql() {
		me := mysql.NewStockInfo()
		me.DecodeFromGin(c)
		if me.ID <= 0 {
			return common.ParamError, "not found id field!"
		}
		if me.Update() {
			return common.DBError, "update failed!"
		}
		return common.Success, "update ok"
	}
	return common.OtherError, "update failed, other error!"
}
