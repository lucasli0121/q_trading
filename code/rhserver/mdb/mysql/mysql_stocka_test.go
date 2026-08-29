package mysql

import (
	"rhserver/cfg"
	"rhserver/mdb/common"
	"testing"
	"time"
)

var stockId int64 = 0

func TestStockInfoInsert(t *testing.T) {
	err := cfg.InitConfig("../../cfg/cfg.yml")
	if err != nil {
		t.Error("initialize config failed, ", err)
		return
	}
	if !Open() {
		t.Error("open database failed")
		return
	}
	defer Close()
	industry := "test概念"
	markDt := common.GetNowDate()
	updateDt := common.GetNowDate()
	me := NewStockInfo()
	me.Code = "0009999"
	me.Name = "testStockInfo"
	me.Industry = &industry
	me.TotalShares = 1.0
	me.CirculShares = 0.0
	me.TotalCap = 0.0
	me.CirculCap = 0.0
	me.MarketDate = &markDt
	me.HisUpdateDate = &updateDt
	me.Insert()
	if me.ID <= 0 {
		t.Errorf("insert stockinfo failed id: %d", me.ID)
	}
	stockId = me.ID
}

func TestStockInfoUpdate(t *testing.T) {
	if stockId <= 0 {
		t.Error("test stockinfo update failed id <=0")
		return
	}
	err := cfg.InitConfig("../../cfg/cfg.yml")
	if err != nil {
		t.Error("initialize config failed, ", err)
		return
	}
	if !Open() {
		t.Error("open database failed")
		return
	}
	defer Close()
	me := NewStockInfo()
	if !me.QueryByID(stockId) {
		t.Errorf("query stockinfo failed id: %d", stockId)
		return
	}
	me.TotalCap++
	updateDt := common.GetNowDate()
	me.HisUpdateDate = &updateDt
	if !me.Update() {
		t.Errorf("update stockinfo failed id:%d", stockId)
	}
}

// 测试删除stockinfo
func TestStockInfoDelete(t *testing.T) {
	if stockId <= 0 {
		t.Error("test stockinfo update failed id <=0")
		return
	}
	err := cfg.InitConfig("../../cfg/cfg.yml")
	if err != nil {
		t.Error("initialize config failed, ", err)
		return
	}
	if !Open() {
		t.Error("open database failed")
		return
	}
	defer Close()
	me := NewStockInfo()
	me.SetID(stockId)
	if !me.Delete() {
		t.Errorf("delete stockinfo failed id:%d", stockId)
	}
}

// 测试添加stockhq数据
var hqId int64 = 0
var stockCode string = "T000000"

func TestInsertStockRealTimeData(t *testing.T) {
	me := NewStockRealTimeData()
	me.Code = stockCode
	me.Name = "股票测试"
	me.Price = 1.0
	me.PChg = 0.1
	me.ChgAmount = 1000
	me.Volume = 10000
	me.Amount = 20000
	me.High = 1.6
	me.Low = 0.9
	me.Open = 1.5
	me.PreClose = 1.0
	me.TurnOver = 10
	me.PE = 12.0
	me.Cap = 1000000
	me.FCap = 50000
	me.ChgInYear = 9
	me.DateTime = time.Now().Format(cfg.TmFmtStr)
	err := cfg.InitConfig("../../cfg/cfg.yml")
	if err != nil {
		t.Error("initialize config failed, ", err)
		return
	}
	if !Open() {
		t.Error("open database failed")
		return
	}
	defer Close()
	if !me.Insert() {
		t.Error("insert stockhq failed")
		return
	}
	hqId = me.ID
}

func TestUpdateStockRealTimeData(t *testing.T) {
	if hqId <= 0 {
		t.Error("update stock hq failed")
		return
	}
	err := cfg.InitConfig("../../cfg/cfg.yml")
	if err != nil {
		t.Error("initialize config failed, ", err)
		return
	}
	if !Open() {
		t.Error("open database failed")
		return
	}
	defer Close()
	me := NewStockRealTimeData()
	me.Code = stockCode
	if !me.QueryByID(hqId) {
		t.Errorf("query stock hq failed, id:%d", hqId)
		return
	}
	me.DateTime = time.Now().Format(cfg.TmFmtStr)
	if !me.Update() {
		t.Errorf("update stock hq failed, id:%d", hqId)
	}
}

func TestDeleteStockAHq(t *testing.T) {
	if hqId <= 0 {
		t.Error("delete stock hq failed")
		return
	}
	err := cfg.InitConfig("../../cfg/cfg.yml")
	if err != nil {
		t.Error("initialize config failed, ", err)
		return
	}
	if !Open() {
		t.Error("open database failed")
		return
	}
	defer Close()
	me := NewStockRealTimeData()
	me.Code = stockCode
	me.SetID(hqId)
	if !me.Delete() {
		t.Errorf("delete stock hq failed, id:%d", hqId)
	}
}

/*
* 测试历史数据
*
 */
func TestInsertStockHisHq(t *testing.T) {
	err := cfg.InitConfig("../../cfg/cfg.yml")
	if err != nil {
		t.Error("initialize config failed, ", err)
		return
	}
	if !Open() {
		t.Error("open database failed")
		return
	}
	defer Close()
	adjust := "qfq"
	me := NewStockHisHq()
	me.Code = stockCode
	me.Name = "股票测试"
	me.Open = 1.5
	me.High = 1.9
	me.Low = 0.9
	me.Close = 1.8
	me.Volume = 10000
	me.Amount = 20000
	me.PChg = 0.1
	me.ChgAmount = 1000
	me.TurnOver = 10
	me.Adjust = &adjust
	me.Date = time.Now().Format(cfg.DateFmtStr)
	if !me.Insert() {
		t.Error("insert stock history hq failed")
		return
	}
	hqId = me.ID
}

func TestUndateStockHisHq(t *testing.T) {
	if hqId <= 0 {
		t.Error("his stodk id is wrong")
		return
	}
	err := cfg.InitConfig("../../cfg/cfg.yml")
	if err != nil {
		t.Error("initialize config failed, ", err)
		return
	}
	if !Open() {
		t.Error("open database failed")
		return
	}
	defer Close()
	me := NewStockHisHq()
	me.Code = stockCode
	if !me.QueryByID(hqId) {
		t.Errorf("query hisstock failed, id:%d", hqId)
		return
	}
	me.Date = time.Now().Format(cfg.DateFmtStr)
	if !me.Update() {
		t.Errorf("update his stock faled, id:%d", hqId)
	}
}

func TestDeletStockHisHq(t *testing.T) {
	if hqId <= 0 {
		t.Error("his stodk id is wrong")
		return
	}
	err := cfg.InitConfig("../../cfg/cfg.yml")
	if err != nil {
		t.Error("initialize config failed, ", err)
		return
	}
	if !Open() {
		t.Error("open database failed")
		return
	}
	defer Close()
	me := NewStockHisHq()
	me.ID = hqId
	me.Code = stockCode
	if !me.Delete() {
		t.Errorf("delete his stock failed,id:%d", hqId)
	}
}
