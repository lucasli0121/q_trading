/******************************************************************************
 * Author: liguoqiang
 * Date: 2024-04-01 13:37:10
 * LastEditors: liguoqiang
 * LastEditTime: 2024-05-30 20:06:04
 * Description:
********************************************************************************/
/*
 * @Author: liguoqiang
 * @Date: 2022-05-26 15:24:09
 * @LastEditors: liguoqiang
 * @LastEditTime: 2023-02-09 23:29:19
 * @Description:
 */
/*
 * @Author: liguoqiang
 * @Date: 2021-03-10 07:32:00
 * @LastEditors: liguoqiang
 * @LastEditTime: 2023-02-05 12:05:45
 * @Description:
 */

package common

import (
	"bytes"
	"compress/zlib"
	"crypto/md5"
	"fmt"
	"rhserver/cfg"
	"time"
)

// define stock tables or table-prefix
const (
	StockRealTimePrefix = "stock_real_time_"
	StockHisPrefix      = "stock_his_"
	IndexTbl            = "index_tbl"
	IndexHqTbl          = "index_hq"
	StockInfoTbl        = "stock_info_tbl"
	IndexInfoTbl        = "index_info"
	IndexStockTbl       = "index_co_stocks"
)

func StockRealTimeTbl(code string) string {
	return StockRealTimePrefix + code
}

func StockHisHqTbl(code string) string {
	return StockHisPrefix + code
}

// define API operation result code
const (
	Success      = 200
	RepeatData   = -21
	HasExist     = -22
	NoExist      = -23
	NoData       = -24
	NoPermission = -25
	PasswdError  = -26
	ParamError   = -27
	RegisterFail = -28
	UploadError  = -29
	FormatError  = -30
	OtherError   = -99
	DBError      = -100
)

// default size of page, 20 records per page
const DEFAULT_PAGE_SIZE int64 = 20

// define PageDao struct
type PageDao struct {
	PageNo     int64
	PageSize   int64
	TotalPages int64
}

const REAL_TIME_NOTIFY_TOPIC string = "stock/realtime/notify"
const REAL_TIME_STOCK_TOPIC string = "stock/realtime/data"

// 返回一个缺省的Page信息
func NewPageDao(pageNo, pageSize int64) *PageDao {
	return &PageDao{
		PageNo:     pageNo,
		PageSize:   pageSize,
		TotalPages: 0,
	}
}

func MakeStockRealTimeTopic(code string) string {
	return REAL_TIME_STOCK_TOPIC + "/" + code
}

/******************************************************************************
 * function: MakeMD5
 * description: encrypt string with md5
 * return {*}
********************************************************************************/
func MakeMD5(str string) string {
	data := []byte(str)
	md5Inst := md5.New()
	md5Inst.Write(data)
	result := md5Inst.Sum([]byte(""))
	md5Str := fmt.Sprintf("%x", result)
	return md5Str
}

/******************************************************************************
 * function: GetNowTime
 * description: return current time format as "2006-01-02 15:04:05"
 * return {*}
********************************************************************************/
func GetNowTime() string {
	return time.Now().Format(cfg.TmFmtStr)
}

/******************************************************************************
 * function: GetNowDate
 * description: return current time format as "2006-01-02"
 * return {*}
********************************************************************************/
func GetNowDate() string {
	return time.Now().Format(cfg.DateFmtStr)
}

/******************************************************************************
 * function: SecondsToTimeStr
 * description: convert seconds to time string format as "2006-01-02 15:04:05"
 * param {int64} seconds
 * return {*}
********************************************************************************/
func SecondsToTimeStr(seconds int64) string {
	var tm time.Duration = time.Duration(seconds) * time.Second
	return time.Unix(int64(tm.Seconds()), 0).Format(cfg.TmFmtStr)
}

/******************************************************************************
 * function: StrToTime
 * description: convert string to time format as location time
 * param {string} tmStr
 * return {*}
********************************************************************************/
func StrToTime(tmStr string) (time.Time, error) {
	return time.ParseInLocation(cfg.TmFmtStr, tmStr, time.Local)
}

func DecompressBytes(deStr []byte) ([]byte, error) {
	zr, err := zlib.NewReader(bytes.NewBuffer(deStr))
	if err != nil {
		return nil, err
	}
	var buf bytes.Buffer
	_, err = buf.ReadFrom(zr)
	if err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}
