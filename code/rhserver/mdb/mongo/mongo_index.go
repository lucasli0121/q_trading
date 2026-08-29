/*
 * @Author: liguoqiang
 * @Date: 2022-05-30 23:25:52
 * @LastEditors: liguoqiang
 * @LastEditTime: 2023-02-12 11:16:49
 * @Description:
 */
package mongo

import (
	"net/http"
	"rhserver/cfg"
	"rhserver/exception"
	"rhserver/mdb/common"
	"time"

	mylog "rhserver/log"

	"github.com/gin-gonic/gin"
	"github.com/gin-gonic/gin/binding"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
)

/*
* Index... 指数表
 */
type Index struct {
	ID        primitive.ObjectID `bson:"_id" json:"id" form:"id" binding:"omitempty"`
	Code      string             `json:"code" form:"code" binding:"required"`
	Name      string             `json:"name" form:"name" binding:"required"`
	BeginDate string             `json:"begin_date" bson:"begin_date" binding:"omitempty"`
	EndDate   string             `json:"end_date" bson:"end_date" binding:"omitempty"`
}

/*
* NewIndex...
 */
func NewIndex() *Index {
	return &Index{
		ID:        primitive.NilObjectID,
		Code:      "",
		Name:      "",
		BeginDate: time.Now().Format(cfg.DateFmtStr),
		EndDate:   time.Now().Format(cfg.DateFmtStr),
	}
}

/*
* QueryAllIndex...
 */
func QueryAllIndex(results *[]Index) bool {
	res := QueryDao(common.IndexTbl, bson.M{}, func(cur *mongo.Cursor) {
		var v *Index = NewIndex()
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
 */
func QueryIndexByCond(filter bson.M, results *[]Index) bool {
	res := QueryDao(common.IndexTbl, filter, func(cur *mongo.Cursor) {
		var v *Index = NewIndex()
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
Decode 解析从gin获取的数据
*/
func (me *Index) Decode(c *gin.Context) {
	if err := c.ShouldBindWith(me, binding.JSON); err != nil {
		exception.Throw(http.StatusAccepted, "Binding error!")
	}
	if me.Code == "" {
		exception.Throw(http.StatusAccepted, "code is empty!")
	}
}

/*
QueryByID() 查询指数表
*/
func (index *Index) QueryByID(id primitive.ObjectID) bool {
	index.SetID(id)
	return QueryDaoByID(common.IndexTbl, index.ID, index)
}

/*
Insert 指数表插入
*/
func (index *Index) Insert() bool {
	index.ID = primitive.NewObjectID()
	return InsertDao(common.IndexTbl, index)
}

/*
Update() 更新指数表
*/
func (index *Index) Update() bool {
	u := bson.M{
		"$set": bson.M{
			"code":       index.Code,
			"name":       index.Name,
			"begin_date": index.BeginDate,
			"end_date":   index.EndDate,
		},
	}
	return UpdateDaoByID(common.IndexTbl, index.ID, u)
}

/*
Delete() 删除指数
*/
func (index *Index) Delete() bool {
	return DeleteDaoByID(common.IndexTbl, index.ID)
}

/*
设置ID
*/
func (index *Index) SetID(id primitive.ObjectID) {
	index.ID = id
}

// swagger:model IndexHq
type IndexHq struct {
	ID        primitive.ObjectID `bson:"_id" json:"id,omitempty" form:"id"`
	Code      string             `json:"code" binding:"required"`
	Name      string             `json:"name" binding:"required"`
	Open      float32            `json:"open" binding:"required"`
	Close     float32            `json:"close" binding:"required"`
	High      float32            `json:"high" binding:"required"`
	Low       float32            `json:"low" binding:"required"`
	ChgAmount float32            `json:"chgamount" binding:"required"`
	PerChg    float32            `json:"perchg" binding:"required"`
	Volume    float32            `json:"volume" binding:"required"`
	Amount    float32            `json:"amount" binding:"required"`
	Adjust    string             `json:"adjust" binding:"omitempty"`
	Date      string             `json:"date" binding:"omitempty"`
}

/*
* NewIndexHq...
 */
func NewIndexHq() *IndexHq {
	return &IndexHq{
		ID:        primitive.NilObjectID,
		Code:      "",
		Name:      "",
		Open:      0.0,
		Close:     0.0,
		High:      0.0,
		Low:       0.0,
		ChgAmount: 0.0,
		PerChg:    0.0,
		Volume:    0.0,
		Amount:    0.0,
		Adjust:    "",
		Date:      time.Now().Format(cfg.DateFmtStr),
	}
}

/*
* QueryAllIndexHq...
 */
func QueryAllIndexHq(results *[]IndexHq) bool {
	res := QueryDao(common.IndexHqTbl, bson.M{}, func(cur *mongo.Cursor) {
		var v *IndexHq = NewIndexHq()
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
QueryIndexHqByCond... 根据条件查询指数行情
*/
func QueryIndexHqByCond(filter interface{}, results *[]IndexHq) bool {
	res := QueryDao(common.IndexHqTbl, filter, func(cur *mongo.Cursor) {
		var v *IndexHq = NewIndexHq()
		err := cur.Decode(v)
		if err != nil {
			mylog.Log.Errorln(err)
		} else {
			*results = append(*results, *v)
		}
	})
	return res
}

func (me *IndexHq) Decode(c *gin.Context) {
	if err := c.ShouldBindWith(me, binding.JSON); err != nil {
		exception.Throw(http.StatusAccepted, "Binding error!")
	}
	if me.Code == "" {
		exception.Throw(http.StatusAccepted, "code is empty!")
	}
	if me.Date == "" {
		exception.Throw(http.StatusAccepted, "date is wrong!")
	}
}

/*
QueryByID() 查询指数行情表
*/
func (me *IndexHq) QueryByID(id primitive.ObjectID) bool {
	me.ID = id
	return QueryDaoByID(common.IndexHqTbl, me.ID, me)
}

func (me *IndexHq) SetID(id primitive.ObjectID) {
	me.ID = id
}

/*
Insert 指数行情表插入
*/
func (me *IndexHq) Insert() bool {
	me.ID = primitive.NewObjectID()
	return InsertDao(common.IndexHqTbl, me)
}

/*
Update() 更新指数表
*/
func (me *IndexHq) Update() bool {
	u := bson.M{
		"$set": bson.M{
			"code":      me.Code,
			"name":      me.Name,
			"open":      me.Open,
			"close":     me.Close,
			"high":      me.High,
			"low":       me.Low,
			"chgamount": me.ChgAmount,
			"perchg":    me.PerChg,
			"volume":    me.Volume,
			"amount":    me.Amount,
			"date":      me.Date,
		},
	}
	return UpdateDaoByID(common.IndexTbl, me.ID, u)
}

/*
Delete() 删除指数
*/
func (me *IndexHq) Delete() bool {
	return DeleteDaoByID(common.IndexHqTbl, me.ID)
}
