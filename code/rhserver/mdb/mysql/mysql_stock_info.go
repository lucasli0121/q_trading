/*
 * @Author: liguoqiang
 * @Date: 2022-06-15 14:27:42
 * @LastEditors: liguoqiang
 * @LastEditTime: 2023-05-16 19:28:41
 * @Description: 此文件定义股票相关结构
 * 描述股票信息，股票信息包含了股票资金情况，流通市值，总市值，所属行业等
 */

package mysql

import (
	"database/sql"
	"net/http"
	"rhserver/exception"
	mylog "rhserver/log"
	"rhserver/mdb/common"

	"github.com/gin-gonic/gin"
	"github.com/gin-gonic/gin/binding"
)

/******************************************************
* 为mysql 数据库提供的结构
* 股票基本信息结构体
* 股票信息包括：代码，名称，市值，流通，所属行业，指数等
*******************************************************/
// swagger:model StockInfo
type StockInfo struct {
	ID            int64       `json:"id" mysql:"id" key:"true" binding:"omitempty"`
	Code          string      `json:"code" mysql:"code" size:"32" unique:"true" binding:"required"`
	Name          string      `json:"name" mysql:"name" size:"32" binding:"required"`
	Industry      *string     `json:"industry" mysql:"industry" size:"64"`
	TotalShares   float64JSON `json:"total_shares" mysql:"total_shares"`
	CirculShares  float64JSON `json:"circul_shares" mysql:"circul_shares"`
	TotalCap      float64JSON `json:"total_cap" mysql:"total_cap"`
	CirculCap     float64JSON `json:"circul_cap" mysql:"circul_cap"`
	MarketDate    *string     `json:"market_date" mysql:"market_date" binding:"datetime=2006-01-02"`
	HisUpdateDate *string     `json:"his_update_date" mysql:"his_update_date" binding:"datetime=2006-01-02"`
}

func NewStockInfo() *StockInfo {
	tsNow := common.GetNowDate()
	return &StockInfo{
		ID:            0,
		Code:          "",
		Name:          "",
		Industry:      nil,
		TotalShares:   0.0,
		CirculShares:  0.0,
		TotalCap:      0.0,
		CirculCap:     0.0,
		MarketDate:    &tsNow,
		HisUpdateDate: &tsNow,
	}
}

/*
*  QueryAllStockInfo...
*  查询所有股票基本信息
 */
func QueryAllStockInfo(results *[]StockInfo) bool {
	res := QueryDao(common.StockInfoTbl, nil, nil, nil, func(rows *sql.Rows) {
		var v *StockInfo = NewStockInfo()
		err := v.DecodeFromRows(rows)
		if err != nil {
			mylog.Log.Errorln(err)
		} else {
			*results = append(*results, *v)
		}
	})
	return res
}

/*
QueryStockInfoByCond...
根据条件查询股票基本信息
*/
func QueryStockInfoByCond(filter interface{}, page *common.PageDao, sort interface{}, results *[]StockInfo) bool {
	res := false
	backFunc := func(rows *sql.Rows) {
		obj := NewStockInfo()
		err := obj.DecodeFromRows(rows)
		if err != nil {
			mylog.Log.Errorln(err)
		} else {
			*results = append(*results, *obj)
		}
	}
	if page == nil {
		res = QueryDao(common.StockInfoTbl, filter, sort, nil, backFunc)
	} else {
		res = QueryPage(common.StockInfoTbl, page, filter, sort, backFunc)
	}
	return res
}

func (me *StockInfo) DecodeFromRows(rows *sql.Rows) error {
	err := rows.Scan(&me.ID,
		&me.Code,
		&me.Name,
		&me.Industry,
		&me.TotalShares,
		&me.CirculShares,
		&me.TotalCap,
		&me.CirculCap,
		&me.MarketDate,
		&me.HisUpdateDate)
	return err
}
func (me *StockInfo) DecodeFromRow(row *sql.Row) error {
	err := row.Scan(&me.ID,
		&me.Code,
		&me.Name,
		&me.Industry,
		&me.TotalShares,
		&me.CirculShares,
		&me.TotalCap,
		&me.CirculCap,
		&me.MarketDate,
		&me.HisUpdateDate)
	return err
}

/*
Decode 解析从gin获取的数据 转换成StockInfo
*/
func (me *StockInfo) DecodeFromGin(c *gin.Context) {
	if err := c.ShouldBindBodyWith(me, binding.JSON); err != nil {
		exception.Throw(http.StatusAccepted, err.Error())
	}
	if me.Code == "" {
		exception.Throw(http.StatusAccepted, "code is empty!")
	}
	if me.Name == "" {
		exception.Throw(http.StatusAccepted, "name is empty!")
	}
}

/*
QueryByID() 查询股票基本信息
*/
func (me *StockInfo) QueryByID(id int64) bool {
	return QueryDaoByID(common.StockInfoTbl, id, me)
}

/*
Insert 股票基本信息数据插入
*/
func (me *StockInfo) Insert() bool {
	if !CheckTableExist(common.StockInfoTbl) {
		CreateTableWithDao(common.StockInfoTbl, me)
	}
	return InsertDao(common.StockInfoTbl, me)
}

/*
Update() 更新股票基本信息
*/
func (me *StockInfo) Update() bool {
	return UpdateDaoByID(common.StockInfoTbl, me.ID, me)
}

/*
Delete() 删除指数
*/
func (me *StockInfo) Delete() bool {
	return DeleteDaoByID(common.StockInfoTbl, me.ID)
}

/*
设置ID
*/
func (me *StockInfo) SetID(id int64) {
	me.ID = id
}
