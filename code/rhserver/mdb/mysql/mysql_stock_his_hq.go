/*
 * @Author: liguoqiang
 * @Date: 2022-06-15 14:27:42
 * @LastEditors: liguoqiang
 * @LastEditTime: 2023-05-16 19:28:41
 * @Description: define stock day data struct
 * adjust 为复权类型，qfq 前复权，hfq 后复权，空为不复权
 */
package mysql

import (
	"database/sql"
	"fmt"
	"net/http"
	"rhserver/cfg"
	"rhserver/exception"
	mylog "rhserver/log"
	"rhserver/mdb/common"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/gin-gonic/gin/binding"
)

/*******************************************************************************
* 定义股票历史行情结构
******************************************************************************/
// swagger:model StockHisHq
type StockHisHq struct {
	ID        int64       `json:"id" mysql:"id" key:"true" binding:"omitempty"`
	Code      string      `json:"code" mysql:"code" binding:"required" size:"32" common:"代码" `
	Name      string      `json:"name" mysql:"name" binding:"required" size:"32" common:"名称" `
	Period    string      `json:"period" mysql:"period" size:"32" common:"K线频率 日线daily 周线weekly 月线monthly" `
	Open      float64JSON `json:"open" mysql:"open" common:"开盘价" `
	Close     float64JSON `json:"close" mysql:"close" common:"收盘价" `
	High      float64JSON `json:"high" mysql:"high" common:"最高价" `
	Low       float64JSON `json:"low" mysql:"low" common:"最低价" `
	Volume    float64JSON `json:"volume" mysql:"volume" common:"成交量" `
	Amount    float64JSON `json:"amount" mysql:"amount" common:"成交额" `
	PChg      float64JSON `json:"pchg" mysql:"pchg" common:"涨跌幅" `
	ChgAmount float64JSON `json:"chgamount" mysql:"chgamount" common:"涨跌额" `
	TurnOver  float64JSON `json:"turnover" mysql:"turnover" common:"换手率" `
	Adjust    *string     `json:"adjust" mysql:"adjust" size:"16" binding:"omitempty" common:"复权：前复权(qfq) 后复权(hfq) 除权(空)" `
	Date      string      `json:"date" mysql:"createdate" unique:"true" binding:"datetime=2006-01-02" common:"新增日期" `
}

/*
StockHisHq...
构造实例
*/
func NewStockHisHq() *StockHisHq {
	adjust := "qfq"
	return &StockHisHq{
		ID:        0,
		Code:      "",
		Name:      "",
		Period:    "daily",
		Open:      0.0,
		Close:     0.0,
		High:      0.0,
		Low:       0.0,
		Volume:    0.0,
		Amount:    0.0,
		PChg:      0.0,
		ChgAmount: 0.0,
		TurnOver:  0.0,
		Adjust:    &adjust,
		Date:      time.Now().Format(cfg.DateFmtStr),
	}
}

/******************************************************************************
 * function: MustNeedQryCond
 * description: define a query condition that must be used
 * param {string} p
 * param {string} a
 * return {*}
********************************************************************************/
func MustNeedQryCond(p string, a string) string {
	period := "daily"
	adjust := "qfq"
	if len(p) > 0 {
		period = p
	}
	if len(a) > 0 {
		adjust = a
	}
	cond := fmt.Sprintf(" period='%s' and adjust='%s'", period, adjust)
	return cond
}

/*
QueryStockHisHqByCond...
根据条件查询历史行情数据
查询条件有：股票代码，名称，起始结束时间
*/
func QueryStockHisHqByCond(code string, filter interface{}, page *common.PageDao, sort interface{}, results *[]StockHisHq) bool {
	res := false
	backFunc := func(rows *sql.Rows) {
		obj := NewStockHisHq()
		err := obj.DecodeFromRows(rows)
		if err != nil {
			mylog.Log.Errorln(err)
		} else {
			*results = append(*results, *obj)
		}
	}
	if page == nil {
		res = QueryDao(common.StockHisHqTbl(code), filter, sort, nil, backFunc)
	} else {
		res = QueryPage(common.StockHisHqTbl(code), page, filter, sort, backFunc)
	}
	return res
}

/*
Decode 解析从gin获取的数据 转换成StockHisHq
*/
func (me *StockHisHq) DecodeFromGin(c *gin.Context) {
	if err := c.ShouldBindBodyWith(me, binding.JSON); err != nil {
		exception.Throw(http.StatusAccepted, err.Error())
	}
	if me.Code == "" {
		exception.Throw(http.StatusAccepted, "code is empty!")
	}
}
func (me *StockHisHq) DecodeFromRows(rows *sql.Rows) error {
	return rows.Scan(&me.ID,
		&me.Code,
		&me.Name,
		&me.Period,
		&me.Open,
		&me.Close,
		&me.High,
		&me.Low,
		&me.Volume,
		&me.Amount,
		&me.PChg,
		&me.ChgAmount,
		&me.TurnOver,
		&me.Adjust,
		&me.Date)
}
func (me *StockHisHq) DecodeFromRow(row *sql.Row) error {
	return row.Scan(&me.ID,
		&me.Code,
		&me.Name,
		&me.Period,
		&me.Open,
		&me.Close,
		&me.High,
		&me.Low,
		&me.Volume,
		&me.Amount,
		&me.PChg,
		&me.ChgAmount,
		&me.TurnOver,
		&me.Adjust,
		&me.Date)
}

/*
QueryByID() 查询股票实时行情
*/
func (me *StockHisHq) QueryByID(id int64) bool {
	me.SetID(id)
	return QueryDaoByID(common.StockHisHqTbl(me.Code), me.ID, me)
}

/*
Insert 股票行情数据插入
*/
func (me *StockHisHq) Insert() bool {
	tblName := common.StockHisHqTbl(me.Code)
	if !CheckTableExist(tblName) {
		CreateTableWithDao(tblName, me)
	}
	return InsertDao(tblName, me)
}

/*
Update() 更新指数表
*/
func (me *StockHisHq) Update() bool {
	return UpdateDaoByID(common.StockHisHqTbl(me.Code), me.ID, me)
}

/*
Delete() 删除指数
*/
func (me *StockHisHq) Delete() bool {
	return DeleteDaoByID(common.StockHisHqTbl(me.Code), me.ID)
}

/*
设置ID
*/
func (me *StockHisHq) SetID(id int64) {
	me.ID = id
}
