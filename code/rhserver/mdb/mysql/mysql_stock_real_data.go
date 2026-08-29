/*
 * @Author: liguoqiang
 * @Date: 2022-06-15 14:27:42
 * @LastEditors: liguoqiang
 * @LastEditTime: 2023-05-16 19:28:41
 * @Description: define stock real-time data struct in minutes
 */
package mysql

import (
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"rhserver/cfg"
	"rhserver/exception"
	"rhserver/gopool"
	mylog "rhserver/log"
	"rhserver/mdb/common"
	"rhserver/mdb/redis"
	"rhserver/mq"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/gin-gonic/gin/binding"
)

type StockRealDataMQProc struct {
}

func (me *StockRealDataMQProc) HandleMqttMsg(topic string, payload []byte) {
	mylog.Log.Infoln("StockRealDataMQProc HandleMqttMsg:", topic, string(payload))
	type StockRealTimeNotify struct {
		RealDate string `json:"real_date"`
	}
	var notify StockRealTimeNotify
	err := json.Unmarshal(payload, &notify)
	if err != nil {
		mylog.Log.Errorln(err)
		return
	}
	// 从redis中获取所有股票代码
	results, err := redis.GetLValueFromList(notify.RealDate, -1, true)
	if err != nil {
		mylog.Log.Errorln(err)
		return
	}
	for _, res := range results {
		baseDecoderStr, err := base64.StdEncoding.DecodeString(res)
		if err != nil {
			mylog.Log.Errorln(err)
			continue
		}
		value, err := common.DecompressBytes(baseDecoderStr)
		if err != nil {
			mylog.Log.Errorln(err)
			continue
		}
		var stockRealTimeData []StockRealTimeData
		err = json.Unmarshal(value, &stockRealTimeData)
		if err != nil {
			mylog.Log.Errorln(err)
			continue
		}
		// 保存到mysql中
		for _, v := range stockRealTimeData {
			taskPool.Put(&gopool.Task{
				Params: []interface{}{v},
				Do: func(params ...interface{}) {
					var obj = params[0].(StockRealTimeData)
					obj.Insert()
				},
			})
			mq.PublishData(common.MakeStockRealTimeTopic(v.Code), v)
		}
	}

}
func NewStockRealDataMQProc() *StockRealDataMQProc {
	return &StockRealDataMQProc{}
}

/******************************************************************************
 * function:
 * description: StockRealTimeData 股票实时行情信息表, 就是每天的分时数据
 * return {*}
********************************************************************************/
// swagger:model StockRealTimeData
type StockRealTimeData struct {
	ID        int64       `json:"id" mysql:"id" binding:"omitempty"`
	Code      string      `json:"code" mysql:"code" binding:"required" size:"32" common:"代码" `
	Name      string      `json:"name" mysql:"name" binding:"required" size:"32" common:"名称" `
	Price     float64JSON `json:"price" mysql:"price" common:"当前价格" `
	PChg      float64JSON `json:"pchg" mysql:"pchg" common:"涨跌幅" `
	ChgAmount float64JSON `json:"chgamount" mysql:"chgamount" common:"涨跌额" `
	Volume    float64JSON `json:"volume" mysql:"volume" common:"成交量" `
	Amount    float64JSON `json:"amount" mysql:"amount" common:"成交额" `
	High      float64JSON `json:"high" mysql:"high" common:"最高价" `
	Low       float64JSON `json:"low" mysql:"low" common:"最低价" `
	Open      float64JSON `json:"open" mysql:"open" common:"开盘价" `
	PreClose  float64JSON `json:"preclose" mysql:"preclose" common:"前日收盘价" `
	TurnOver  float64JSON `json:"turnover" mysql:"turnover" common:"换手率" `
	PE        float64JSON `json:"pe" mysql:"pe" common:"动态市盈率" `
	Cap       float64JSON `json:"cap" mysql:"cap" common:"市值" `
	FCap      float64JSON `json:"fcap" mysql:"fcap" common:"流通市值" `
	ChgInYear float64JSON `json:"chginyear" mysql:"chginyear" common:"本年最大涨幅" `
	DateTime  string      `json:"createtime" mysql:"createtime" binding:"datetime=2006-01-02 15:04:05"`
}

/*
NewStockRealTimeData...
构造实例
*/
func NewStockRealTimeData() *StockRealTimeData {
	return &StockRealTimeData{
		ID:        0,
		Code:      "",
		Name:      "",
		Price:     0.0,
		PChg:      0.0,
		ChgAmount: 0.0,
		Volume:    0.0,
		Amount:    0.0,
		High:      0.0,
		Low:       0.0,
		Open:      0.0,
		PreClose:  0.0,
		TurnOver:  0.0,
		PE:        0.0,
		Cap:       0.0,
		FCap:      0.0,
		ChgInYear: 0.0,
		DateTime:  time.Now().Format(cfg.TmFmtStr),
	}
}

/******************************************************************************
 * function: QueryLatestStockAByCode
 * description: 查询对应代码的最近一条实时行情数据
 * param {string} code
 * param {*[]StockRealTimeData} results
 * return {*}
********************************************************************************/
func QueryLatestStockRealTimeDataByCode(code string, results *[]StockRealTimeData) bool {
	res := QueryDao(common.StockRealTimeTbl(code), nil, "createtime desc", "1", func(rows *sql.Rows) {
		var v *StockRealTimeData = NewStockRealTimeData()
		err := v.DecodeFromRows(rows)
		if err != nil {
			mylog.Log.Errorln(err)
		} else {
			*results = append(*results, *v)
		}
	})
	return res
}

/******************************************************************************
 * function:
 * description: 根据条件查询实时行情数据
 * param {string} code
 * param {interface{}} filter
 * param {*common.PageDao} page
 * param {interface{}} sort
 * param {*[]StockAHq} results
 * return {*}
********************************************************************************/
func QueryStockRealTimeDataByCond(code string, filter interface{}, page *common.PageDao, sort interface{}, results *[]StockRealTimeData) bool {
	res := false
	backFunc := func(rows *sql.Rows) {
		obj := NewStockRealTimeData()
		err := obj.DecodeFromRows(rows)
		if err != nil {
			mylog.Log.Errorln(err)
		} else {
			*results = append(*results, *obj)
		}
	}
	if page == nil {
		res = QueryDao(common.StockRealTimeTbl(code), filter, sort, nil, backFunc)
	} else {
		res = QueryPage(common.StockRealTimeTbl(code), page, filter, sort, backFunc)
	}
	return res
}

/******************************************************************************
 * function: QueryAllStocksRealTimeData
 * description: At first query all stock info
 * than query at latest real time data limit 1
 * param {*common.PageDao} page
 * param {*[]StockRealTimeData} results
 * return {*}
********************************************************************************/
func QueryAllStocksRealTimeData(page *common.PageDao, results *[]StockRealTimeData) {
	var stockList []StockInfo
	if QueryStockInfoByCond(nil, page, "his_update_date desc", &stockList) {
		for _, v := range stockList {
			var hqLst []StockRealTimeData
			if QueryLatestStockRealTimeDataByCode(v.Code, &hqLst) {
				*results = append(*results, hqLst...)
			}
		}
	}
}

/*
Decode 解析从gin获取的数据 转换成StockAHq
*/
func (me *StockRealTimeData) DecodeFromGin(c *gin.Context) {
	if err := c.ShouldBindBodyWith(me, binding.JSON); err != nil {
		exception.Throw(http.StatusAccepted, err.Error())
	}
	if me.Code == "" {
		exception.Throw(http.StatusAccepted, "code is empty!")
	}
}

func (me *StockRealTimeData) DecodeFromRows(rows *sql.Rows) error {
	return rows.Scan(&me.ID,
		&me.Code,
		&me.Name,
		&me.Price,
		&me.PChg,
		&me.ChgAmount,
		&me.Volume,
		&me.Amount,
		&me.High,
		&me.Low,
		&me.Open,
		&me.PreClose,
		&me.TurnOver,
		&me.PE,
		&me.Cap,
		&me.FCap,
		&me.ChgInYear,
		&me.DateTime)
}
func (me *StockRealTimeData) DecodeFromRow(row *sql.Row) error {
	return row.Scan(&me.ID,
		&me.Code,
		&me.Name,
		&me.Price,
		&me.PChg,
		&me.ChgAmount,
		&me.Volume,
		&me.Amount,
		&me.High,
		&me.Low,
		&me.Open,
		&me.PreClose,
		&me.TurnOver,
		&me.PE,
		&me.Cap,
		&me.FCap,
		&me.ChgInYear,
		&me.DateTime)
}

/*
QueryByID() 查询股票实时行情
*/
func (me *StockRealTimeData) QueryByID(id int64) bool {
	me.SetID(id)
	return QueryDaoByID(common.StockRealTimeTbl(me.Code), me.ID, me)
}

/*
Insert 股票行情数据插入
*/
func (me *StockRealTimeData) Insert() bool {
	tblName := common.StockRealTimeTbl(me.Code)
	if !CheckTableExist(tblName) {
		CreateTableWithDao(tblName, me)
	}
	return InsertDao(tblName, me)
}

func (me *StockRealTimeData) Update() bool {
	return UpdateDaoByID(common.StockRealTimeTbl(me.Code), me.ID, me)
}

func (me *StockRealTimeData) Delete() bool {
	return DeleteDaoByID(common.StockRealTimeTbl(me.Code), me.ID)
}

/*
设置ID
*/
func (me *StockRealTimeData) SetID(id int64) {
	me.ID = id
}
