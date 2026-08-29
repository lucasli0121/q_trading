/*
 * @Author: liguoqiang
 * @Date: 2022-06-15 14:27:42
 * @LastEditors: liguoqiang
 * @LastEditTime: 2023-04-21 09:28:28
 * @Description:
 */
/**********************************************************
* 此文件定义股票相关结构
* 包含： 股票信息，股票综述，股票行情，股票历史等
**********************************************************/
package mongo

import (
	"fmt"
	"math"
	"net/http"
	"reflect"
	"rhserver/cfg"
	"rhserver/exception"
	mylog "rhserver/log"
	"rhserver/mdb/common"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/gin-gonic/gin/binding"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"
)

/*
* Dao... Mongo所有数据对象的基类
 */
type Dao interface {
	QueryByID(primitive.ObjectID) bool
	Insert() bool
	Update() bool
	Delete() bool
	SetID(primitive.ObjectID)
}

type ASummary struct {
	ID   primitive.ObjectID `json:"id" bson:"id" binding:"omitempty"`
	Name string             `json:"name"`
}

/******************************************************
* 为mongo提供的结构
* 股票基本信息结构体
* 股票信息包括：代码，名称，市值，流通，所属行业，指数等
*******************************************************/
type StockInfo struct {
	ID            primitive.ObjectID `json:"id" bson:"_id" binding:"omitempty"`
	Code          string             `json:"code" binding:"required"`
	Name          string             `json:"name" binding:"required"`
	Industry      string             `json:"industry"`
	TotalShares   float64            `json:"total_shares" bson:"total_shares"`
	CirculShares  float64            `json:"circul_shares" bson:"circul_shares"`
	TotalCap      float64            `json:"total_cap" bson:"total_cap"`
	CirculCap     float64            `json:"circul_cap" bson:"circul_cap"`
	MarketDate    string             `json:"market_date" bson:"market_date"`
	HisUpdateDate string             `json:"his_update_date" bson:"his_update_date"`
}

func NewStockInfo() *StockInfo {
	return &StockInfo{
		ID:           primitive.NilObjectID,
		Code:         "",
		Name:         "",
		Industry:     "",
		TotalShares:  0.0,
		CirculShares: 0.0,
		TotalCap:     0.0,
		CirculCap:    0.0,
		MarketDate:   time.Now().Format(cfg.DateFmtStr),
	}
}

func (si *StockInfo) convertFromBsonD(bs bson.D) error {
	var err error = nil
	for _, v := range bs {
		switch v.Key {
		case "_id":
			copy(si.ID[:], []byte(fmt.Sprintf("%v", v.Value)))
		case "code":
			si.Code = v.Value.(string)
		case "name":
			si.Name = v.Value.(string)
		case "total_shares":
			si.TotalShares = v.Value.(float64)
		case "total_cap":
			si.TotalCap = v.Value.(float64)
		case "circul_cap":
			si.CirculCap = v.Value.(float64)
		}
	}
	return err
}

/*
*  QueryAllStockInfo...
*  查询所有股票基本信息
 */
func QueryAllStockInfo(results *[]StockInfo) bool {
	res := QueryDao(common.StockInfoTbl, bson.M{}, func(cur *mongo.Cursor) {
		var v *StockInfo = NewStockInfo()
		err := cur.Decode(v)
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
func QueryStockInfoByCond(filter bson.M, page *common.PageDao, sort bson.M, results *[]StockInfo) bool {
	return queryStockDaoByCond(common.StockInfoTbl, NewStockInfo(), filter, page, sort, results)
}

/*
Decode 解析从gin获取的数据 转换成StockInfo
*/
func (me *StockInfo) Decode(c *gin.Context) {
	if err := c.ShouldBindWith(me, binding.JSON); err != nil {
		exception.Throw(http.StatusAccepted, "Binding error!")
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
func (me *StockInfo) QueryByID(id primitive.ObjectID) bool {
	me.SetID(id)
	return QueryDaoByID(common.StockInfoTbl, me.ID, me)
}

/*
Insert 股票基本信息数据插入
*/
func (me *StockInfo) Insert() bool {
	me.ID = primitive.NewObjectID()
	return InsertDao(common.StockInfoTbl, me)
}

/*
Update() 更新股票基本信息
*/
func (me *StockInfo) Update() bool {
	u := bson.M{
		"$set": bson.M{
			"code":            me.Code,
			"name":            me.Name,
			"industry":        me.Industry,
			"total_shares":    me.TotalShares,
			"circul_shares":   me.CirculShares,
			"total_cap":       me.TotalCap,
			"circul_cap":      me.CirculCap,
			"market_date":     me.MarketDate,
			"his_update_date": me.HisUpdateDate,
		},
	}
	return UpdateDaoByID(common.StockInfoTbl, me.ID, u)
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
func (me *StockInfo) SetID(id primitive.ObjectID) {
	me.ID = id
}

/*
********************************************************************************

	StockAHq 股票实时行情信息表
	保存股票实时行情数据

********************************************************************************
*/
type StockAHq struct {
	ID        primitive.ObjectID `json:"id" bson:"_id" binding:"omitempty"`
	Code      string             `json:"code" binding:"required"`    // 代码
	Name      string             `json:"name" binding:"required"`    // 名称
	Price     float64            `json:"price"`                      // 当前价格
	PChg      float64            `json:"pchg" bson:"pchg"`           //涨跌幅
	ChgAmount float64            `json:"chgamount" bson:"chgamount"` // 涨跌额
	Volume    float64            `json:"volume"`                     // 成交量
	Amount    float64            `json:"amount"`                     // 成交额
	High      float64            `json:"high"`                       // 最高价
	Low       float64            `json:"low"`                        // 最低价
	Open      float64            `json:"open"`                       // 开盘价
	PreClose  float64            `json:"preclose" bson:"preclose"`   // 前日收盘价
	TurnOver  float64            `json:"turnover" bson:"turnover"`   // 换手率
	PE        float64            `json:"pe"`                         // 动态市盈率
	Cap       float64            `json:"cap"`                        // 市值
	FCap      float64            `json:"fcap" bson:"fcap"`           // 流通市值
	ChgInYear float64            `json:"chginyear" bson:"chginyear"` // 本年最大涨幅
	DateTime  string             `json:"datetime" bson:"datetime" binding:"datetime=2006-01-02 15:04:05"`
}

/*
NewStockAHq...
构造实例
*/
func NewStockAHq() *StockAHq {
	return &StockAHq{
		ID:        primitive.NilObjectID,
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

func (me *StockAHq) MarshalJSON() ([]byte, error) {
	var b []byte
	u := reflect.TypeOf(me)
	vf := reflect.ValueOf(me)
	numField := u.Elem().NumField()
	b = append(b, "{"...)
	for num := 0; num < numField; num++ {
		f := u.Elem().Field(num)
		v := vf.Elem().Field(num)
		switch v.Kind() {
		case reflect.Array:
			var id primitive.ObjectID
			copy(id[:], v.Bytes())
			val := fmt.Sprintf("\"%v\":\"%v\"", f.Tag.Get("json"), id.Hex())
			if num < (numField - 1) {
				val += ","
			}
			b = append(b, val...)
		case reflect.Float64:
			var val string
			if math.IsNaN(v.Float()) {
				val = fmt.Sprintf("\"%v\":\"NaN\"", f.Tag.Get("json"))
			} else {
				val = fmt.Sprintf("\"%v\":\"%v\"", f.Tag.Get("json"), v.Float())
			}
			if num < (numField - 1) {
				val += ","
			}
			b = append(b, val...)
		case reflect.String:
			val := fmt.Sprintf("\"%v\":\"%v\"", f.Tag.Get("json"), v.String())
			if num < (numField - 1) {
				val += ","
			}
			b = append(b, val...)
		}
	}
	b = append(b, "}"...)
	return b, nil
}

// func (me *StockAHq) convertFromBsonD(bs bson.D) error {
// 	var err error = nil
// 	u := reflect.TypeOf(me)
// 	for idx, v := range bs {
// 		f := u.Elem().Field(idx)
// 		n := f.Tag.Get("bson")
// 		switch v.Key {
// 		case "_id":
// 			copy(si.ID[:], []byte(fmt.Sprintf("%v", v.Value)))
// 		case "code":
// 			si.Code = v.Value.(string)
// 		case "name":
// 			si.Name = v.Value.(string)
// 		case "total_shares":
// 			si.TotalShares = v.Value.(float64)
// 		case "total_cap":
// 			si.TotalCap = v.Value.(float64)
// 		case "circul_cap":
// 			si.CirculCap = v.Value.(float64)
// 		}
// 	}
// 	return err
// }

/*
*  QueryLatestStockAByCode...
*  查询对应代码的最近一条实时行情数据
 */
func QueryLatestStockAByCode(code string, results *[]StockAHq) bool {
	opt := &options.FindOptions{}
	opt.SetLimit(1).SetSort(bson.M{"datetime": -1})
	res := QueryDao(common.StockRealTimeTbl(code), bson.M{}, func(cur *mongo.Cursor) {
		var v *StockAHq = NewStockAHq()
		err := cur.Decode(v)
		if err != nil {
			mylog.Log.Errorln(err)
		} else {
			*results = append(*results, *v)
		}
	}, opt)
	return res
}

/*
QueryStockAHqByCond...
根据条件查询实时行情数据
*/
func QueryStockAHqByCond(code string, filter bson.M, page *common.PageDao, sort bson.M, results *[]StockAHq) bool {
	return queryStockDaoByCond(common.StockRealTimeTbl(code), NewStockAHq(), filter, page, sort, results)
}

/*
联合查询，既要查询股票基本信息，还要查询股票当前行情数据，最后返回所有数据
*/
func QueryAllStocksHq(page *common.PageDao, results *[]StockAHq) {
	var stockList []StockInfo
	if QueryStockInfoByCond(nil, page, bson.M{"his_update_date": -1}, &stockList) {
		for _, v := range stockList {
			var hqLst []StockAHq
			if QueryLatestStockAByCode(v.Code, &hqLst) {
				*results = append(*results, hqLst...)
			}
		}
	}
}

/*
Decode 解析从gin获取的数据 转换成StockAHq
*/
func (me *StockAHq) Decode(c *gin.Context) {
	if err := c.ShouldBindWith(me, binding.JSON); err != nil {
		exception.Throw(http.StatusAccepted, "Binding error!")
	}
	if me.Code == "" {
		exception.Throw(http.StatusAccepted, "code is empty!")
	}
}

/*
QueryByID() 查询股票实时行情
*/
func (me *StockAHq) QueryByID(id primitive.ObjectID) bool {
	me.SetID(id)
	return QueryDaoByID(common.StockRealTimeTbl(me.Code), me.ID, me)
}

/*
Insert 股票行情数据插入
*/
func (me *StockAHq) Insert() bool {
	me.ID = primitive.NewObjectID()
	return InsertDao(common.StockRealTimeTbl(me.Code), me)
}

/*
Update() 更新指数表
*/
func (me *StockAHq) Update() bool {
	u := bson.M{
		"$set": bson.M{
			"code":      me.Code,
			"name":      me.Name,
			"price":     me.Price,
			"pchg":      me.PChg,
			"chgamount": me.ChgAmount,
			"volume":    me.Volume,
			"amount":    me.Amount,
			"high":      me.High,
			"low":       me.Low,
			"open":      me.Open,
			"preclose":  me.PreClose,
			"turnover":  me.TurnOver,
			"pe":        me.PE,
			"cap":       me.Cap,
			"fcap":      me.FCap,
			"chginyear": me.ChgInYear,
			"datetime":  me.DateTime,
		},
	}
	return UpdateDaoByID(common.StockRealTimeTbl(me.Code), me.ID, u)
}

/*
Delete() 删除指数
*/
func (me *StockAHq) Delete() bool {
	return DeleteDaoByID(common.StockRealTimeTbl(me.Code), me.ID)
}

/*
设置ID
*/
func (me *StockAHq) SetID(id primitive.ObjectID) {
	me.ID = id
}

/*******************************************************************************
* 定义股票历史行情结构
******************************************************************************/
type StockHisHq struct {
	ID        primitive.ObjectID `bson:"_id" json:"id" binding:"omitempty" form:"id"`
	Code      string             `json:"code" binding:"required"`    // 代码
	Name      string             `json:"name" binding:"required"`    // 名称
	Open      float64            `json:"open"`                       // 开盘价
	Close     float64            `json:"close"`                      // 收盘价
	High      float64            `json:"high"`                       // 最高价
	Low       float64            `json:"low"`                        // 最低价
	Volume    float64            `json:"volume"`                     // 成交量
	Amount    float64            `json:"amount"`                     // 成交额
	PChg      float64            `json:"pchg"`                       // 涨跌幅
	ChgAmount float64            `json:"chgamount"`                  // 涨跌额
	TurnOver  float64            `json:"turnover"`                   // 换手率
	Adjust    string             `json:"adjust" binding:"omitempty"` // 复权：前复权(qfq),后复权(hfq),除权(空)
	Date      string             `json:"date" binding:"omitempty"`
}

/*
StockHisHq...
构造实例
*/
func NewStockHisHq() *StockHisHq {
	return &StockHisHq{
		ID:        primitive.NilObjectID,
		Code:      "",
		Name:      "",
		Open:      0.0,
		Close:     0.0,
		High:      0.0,
		Low:       0.0,
		Volume:    0.0,
		Amount:    0.0,
		PChg:      0.0,
		ChgAmount: 0.0,
		TurnOver:  0.0,
		Adjust:    "",
		Date:      time.Now().Format(cfg.DateFmtStr),
	}
}

func (me *StockHisHq) MarshalJSON() ([]byte, error) {
	var b []byte
	u := reflect.TypeOf(me)
	vf := reflect.ValueOf(me)
	numField := u.Elem().NumField()
	b = append(b, "{"...)
	for num := 0; num < numField; num++ {
		f := u.Elem().Field(num)
		v := vf.Elem().Field(num)
		fmt.Println(f, v)
		switch v.Kind() {
		case reflect.Array:
			var id primitive.ObjectID
			copy(id[:], v.Bytes())
			val := fmt.Sprintf("\"%v\":\"%v\"", f.Tag.Get("json"), id.Hex())
			if num < (numField - 1) {
				val += ","
			}
			b = append(b, val...)
		case reflect.Float64:
			var val string
			if math.IsNaN(v.Float()) {
				val = fmt.Sprintf("\"%v\":0", f.Tag.Get("json"))
			} else {
				val = fmt.Sprintf("\"%v\":%v", f.Tag.Get("json"), v.Float())
			}
			if num < (numField - 1) {
				val += ","
			}
			b = append(b, val...)
		case reflect.String:
			val := fmt.Sprintf("\"%v\":\"%v\"", f.Tag.Get("json"), v.String())
			if num < (numField - 1) {
				val += ","
			}
			b = append(b, val...)
		}
	}
	b = append(b, "}"...)
	return b, nil
}

/*
QueryStockHisHqByCond...
根据条件查询历史行情数据
查询条件有：股票代码，名称，起始结束时间
*/
func QueryStockHisHqByCond(code string, filter bson.M, page *common.PageDao, sort bson.M, results *[]StockHisHq) bool {
	return queryStockDaoByCond(common.StockHisHqTbl(code), NewStockHisHq(), filter, page, sort, results)
}

/*
Decode 解析从gin获取的数据 转换成StockHisHq
*/
func (me *StockHisHq) Decode(c *gin.Context) {
	if err := c.ShouldBindWith(me, binding.JSON); err != nil {
		exception.Throw(http.StatusAccepted, "Binding error!")
	}
	if me.Code == "" {
		exception.Throw(http.StatusAccepted, "code is empty!")
	}
}

/*
QueryByID() 查询股票实时行情
*/
func (me *StockHisHq) QueryByID(id primitive.ObjectID) bool {
	me.SetID(id)
	return QueryDaoByID(common.StockHisHqTbl(me.Code), me.ID, me)
}

/*
Insert 股票行情数据插入
*/
func (me *StockHisHq) Insert() bool {
	me.ID = primitive.NewObjectID()
	return InsertDao(common.StockHisHqTbl(me.Code), me)
}

/*
Update() 更新指数表
*/
func (me *StockHisHq) Update() bool {
	u := bson.M{
		"$set": bson.M{
			"code":      me.Code,
			"name":      me.Name,
			"open":      me.Open,
			"close":     me.Close,
			"pchg":      me.PChg,
			"chgamount": me.ChgAmount,
			"volume":    me.Volume,
			"amount":    me.Amount,
			"high":      me.High,
			"low":       me.Low,
			"turnover":  me.TurnOver,
			"adjust":    me.Adjust,
			"date":      me.Date,
		},
	}
	return UpdateDaoByID(common.StockHisHqTbl(me.Code), me.ID, u)
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
func (me *StockHisHq) SetID(id primitive.ObjectID) {
	me.ID = id
}

/*
 定义一个泛型函数，用来统一处理查询股票行情操作，包括股票历史行情，股票信息，股票实时行情等
*/
// 先定义一个类型接口
type StockDaoT interface {
	StockInfo | StockAHq | StockHisHq
}

func queryStockDaoByCond[T StockDaoT](tableName string, daoObj *T, filter bson.M, page *common.PageDao, sort bson.M, results *[]T) bool {
	res := false
	// 先把results转换成指针数组
	backFunc := func(cur *mongo.Cursor) {
		//var bs bson.D
		err := cur.Decode(daoObj)
		//v.convertFromBsonD(bs)
		if err != nil {
			mylog.Log.Errorln(err)
		} else {
			*results = append(*results, *daoObj)
		}
	}
	if page == nil {
		res = QueryDao(tableName, filter, backFunc)
	} else {
		res = QueryPage(tableName, page, filter, sort, backFunc)
	}
	return res
}
