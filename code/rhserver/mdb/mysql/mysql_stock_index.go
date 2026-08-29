/*
 * @Author: liguoqiang
 * @Date: 2022-06-15 14:27:42
 * @LastEditors: liguoqiang
 * @LastEditTime: 2023-05-16 19:28:41
 * @Description:
 */
/**********************************************************
* 此文件定义股票相关结构
* 包含： 股票信息，股票综述，股票行情，股票历史等
**********************************************************/
package mysql

import (
	"database/sql"
	"rhserver/cfg"
	"rhserver/exception"
	mylog "rhserver/log"
	"rhserver/mdb/common"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/gin-gonic/gin/binding"
)

/*
**********************************************************************************

	定义指数基本数据表以及指数对应成分股表

**********************************************************************************
*/

// swagger:model IndexInfo
type IndexInfo struct {
	ID          int64  `json:"id" mysql:"id" binding:"omitempty"`
	Code        string `json:"code" mysql:"code" size:"32" binding:"required" common:"指数代码" ` // 代码
	Name        string `json:"name" mysql:"name" size:"32" binding:"required" common:"指数名称"`  // 名称
	PublishDate string `json:"publish_date" mysql:"publish_date" binding:"datetime=2006-01-02" common:"发行日期" `
	UpdateDate  string `json:"update_date" mysql:"update_date" binding:"datetime=2006-01-02" common:"更新日期"`
}

func NewIndexInfo() *IndexInfo {
	return &IndexInfo{
		ID:          0,
		Code:        "",
		Name:        "",
		PublishDate: time.Now().Format(cfg.DateFmtStr),
		UpdateDate:  time.Now().Format(cfg.DateFmtStr),
	}
}

/*
*  QueryAllIndexInfo...
*  查询所有指数记录
 */
func QueryAllIndexInfo(results *[]IndexInfo) bool {
	res := QueryDao(common.IndexInfoTbl, nil, nil, nil, func(rows *sql.Rows) {
		var v *IndexInfo = NewIndexInfo()
		err := v.DecodeFromRows(rows)
		if err != nil {
			mylog.Log.Errorln(err)
		} else {
			*results = append(*results, *v)
		}
	})
	return res
}

func (me *IndexInfo) DecodeFromRows(rows *sql.Rows) error {
	return rows.Scan(&me.ID, &me.Code, &me.Name, &me.PublishDate, &me.UpdateDate)
}
func (me *IndexInfo) DecodeFromRow(row *sql.Row) error {
	return row.Scan(&me.ID, &me.Code, &me.Name, &me.PublishDate, &me.UpdateDate)
}

/*
Decode 解析从gin获取的数据 转换成IndexInfo
*/
func (me *IndexInfo) DecodeFromGin(c *gin.Context) {
	if err := c.ShouldBindBodyWith(me, binding.JSON); err != nil {
		mylog.Log.Errorln(err)
		exception.Throw(common.ParamError, err.Error())
	}
	if me.Code == "" {
		exception.Throw(common.ParamError, "code is empty!")
	}
	if me.Name == "" {
		exception.Throw(common.ParamError, "name is empty!")
	}
}

/*
QueryByID() 查询指数基本信息
*/
func (me *IndexInfo) QueryByID(id int64) bool {
	return QueryDaoByID(common.IndexInfoTbl, id, me)
}

/*
Insert 指数基本信息数据插入
*/
func (me *IndexInfo) Insert() bool {
	if !CheckTableExist(common.IndexInfoTbl) {
		CreateTableWithDao(common.IndexInfoTbl, me)
	}
	return InsertDao(common.IndexInfoTbl, me)
}

/*
Update() 更新指数基本信息
*/
func (me *IndexInfo) Update() bool {
	return UpdateDaoByID(common.IndexInfoTbl, me.ID, me)
}

/*
Delete() 删除指数
*/
func (me *IndexInfo) Delete() bool {
	return DeleteDaoByID(common.IndexInfoTbl, me.ID)
}

/*
设置ID
*/
func (me *IndexInfo) SetID(id int64) {
	me.ID = id
}

/*
********************************************************************************

	IndexStock 指数成分股

********************************************************************************
*/
// swagger:model IndexStock
type IndexStock struct {
	ID         int64  `json:"id" mysql:"id" binding:"omitempty"`
	IndexCode  string `json:"index_code" mysql:"index_code" size:"32" binding:"required"` // 代码
	Code       string `json:"code" mysql:"code" size:"32" binding:"required"`             // 代码
	Name       string `json:"name" mysql:"name" size:"32" binding:"required"`             // 名称
	InDate     string `json:"in_date" mysql:"in_date" binding:"datetime=2006-01-02"`
	UpdateDate string `json:"update_date" mysql:"update_date" binding:"datetime=2006-01-02"`
}

func NewIndexStock() *IndexStock {
	return &IndexStock{
		ID:         0,
		Code:       "",
		IndexCode:  "",
		Name:       "",
		InDate:     time.Now().Format(cfg.DateFmtStr),
		UpdateDate: time.Now().Format(cfg.DateFmtStr),
	}
}

/*
*  QueryAllIndexCoStock...
*  查询所有指数成分股
 */
func QueryAllIndexCoStock(results *[]IndexStock) bool {
	res := QueryDao(common.IndexStockTbl, nil, nil, nil, func(rows *sql.Rows) {
		var v *IndexStock = NewIndexStock()
		err := v.DecodeFromRows(rows)
		if err != nil {
			mylog.Log.Errorln(err)
		} else {
			*results = append(*results, *v)
		}
	})
	return res
}

/**
 * @function: QueryIndexCoStockByCond
 * @description: query index co-stock by condition, conditon include index_code, publish_date
 * @return {*}
 */
func QueryIndexCoStockByCond(filter interface{}, sort string, results *[]IndexStock) bool {
	res := false
	backFunc := func(rows *sql.Rows) {
		obj := NewIndexStock()
		err := obj.DecodeFromRows(rows)
		if err != nil {
			mylog.Log.Errorln(err)
		} else {
			*results = append(*results, *obj)
		}
	}
	res = QueryDao(common.IndexStockTbl, filter, sort, nil, backFunc)
	return res
}

func (me *IndexStock) DecodeFromRows(rows *sql.Rows) error {
	return rows.Scan(&me.ID, &me.IndexCode, &me.Code, &me.Name, &me.InDate, &me.UpdateDate)
}
func (me *IndexStock) DecodeFromRow(row *sql.Row) error {
	return row.Scan(&me.ID, &me.IndexCode, &me.Code, &me.Name, &me.InDate, &me.UpdateDate)
}

/*
Decode 解析从gin获取的数据 转换成IndexStock
*/
func (me *IndexStock) DecodeFromGin(c *gin.Context) {
	if err := c.ShouldBindBodyWith(me, binding.JSON); err != nil {
		exception.Throw(common.ParamError, err.Error())
	}
	if me.Code == "" {
		exception.Throw(common.ParamError, "code is empty!")
	}
	if me.Name == "" {
		exception.Throw(common.ParamError, "name is empty!")
	}
}

/*
QueryByID() 查询指数基本信息
*/
func (me *IndexStock) QueryByID(id int64) bool {
	return QueryDaoByID(common.IndexStockTbl, id, me)
}

/*
Insert 指数基本信息数据插入
*/
func (me *IndexStock) Insert() bool {
	if CheckTableExist(common.IndexStockTbl) {
		CreateTableWithDao(common.IndexStockTbl, me)
	}
	return InsertDao(common.IndexStockTbl, me)
}

/*
Update() 更新指数基本信息
*/
func (me *IndexStock) Update() bool {
	return UpdateDaoByID(common.IndexStockTbl, me.ID, me)
}

/*
Delete() 删除指数
*/
func (me *IndexStock) Delete() bool {
	return DeleteDaoByID(common.IndexStockTbl, me.ID)
}

/*
设置ID
*/
func (me *IndexStock) SetID(id int64) {
	me.ID = id
}
