/*********************************************************************
* 此文件实现A股的实时行情数据以及历史数据的操作，包括增删改查等
*
**********************************************************************/

package api

import (
	"rhserver/mdb"

	"github.com/gin-gonic/gin"
)

/*
WEB 接口，此函数用来查询股票实时行情数据
条件： id, code, name 时间
*/
// queryRealTimeHqByCode godoc
//
//	@Summary	queryRealTimeHqByCode
//	@Schemes
//	@Description	查询股票实时行情数据
//	@Tags			stock
//
//	@Param			code	query	string	false	"stock code"
//	@Param			start_time	query	string	false	"query start time"
//	@Param			end_time	query	string	false	"query end time"
//
//	@Produce		json
//	@Success		200	{object}	mysql.StockRealTimeData
//	@Router			/v1/stock/queryRealTimeHqByCode [get]
func queryRealTimeHqByCode(c *gin.Context) {

	page := getPageDaoFromGin(c)
	apiCommonFuncWithPage(c, page, mdb.QueryStockRealTimeByCond)
}

/*
queryAllLatestRealTimeHq... 一次性获取所有股票的实时行情信息，根据分页返回数据
*/
// queryAllLatestRealTimeHq godoc
//
//	@Summary	queryAllLatestRealTimeHq
//	@Schemes
//	@Description	查询股票实时行情数据
//	@Tags			stock
//
//	@Produce		json
//	@Success		200	{object}	mysql.StockRealTimeData
//	@Router			/v1/stock/queryAllLatestRealTimeHq [get]
func queryAllLatestRealTimeHq(c *gin.Context) {
	page := getPageDaoFromGin(c)
	apiCommonFuncWithPage(c, page, mdb.QueryAllLatestRealTimeHq)
}

/*
insertRealTimeHq...
此函数用来插入股票实时行情数据
*/
// insertRealTimeHq godoc
//
//	@Summary	insertRealTimeHq
//	@Schemes
//	@Description	insert stock real-time data
//	@Tags			stock
//	@Produce		json
//
//	@Param			in	body	mysql.StockRealTimeData	 true	"stock real-time data"
//
//	@Success		200			{object}	mysql.StockRealTimeData
//	@Router			/v1/stock/insertRealTimeHq [post]
func insertRealTimeHq(c *gin.Context) {
	apiCommonFunc(c, mdb.InsertStockRealTimeHq)
}

/*
updateRealTimeHq... 根据ID, code,name 更新股票实时行情数据
*/
// updateRealTimeHq godoc
//
//	@Summary	updateRealTimeHq
//	@Schemes
//	@Description	update stock real-time data
//	@Tags			stock
//	@Produce		json
//
//	@Param			in	body	mysql.StockRealTimeData	 true	"stock real-time data"
//
//	@Success		200			{object}	mysql.StockRealTimeData
//	@Router			/v1/stock/updateRealTimeHq [post]
func updateRealTimeHq(c *gin.Context) {
	apiCommonFunc(c, mdb.UpdateStockRealTimeHq)
}

/****************************************************************************
* 股票历史行情操作
*
****************************************************************************/

/*
WEB 接口，此函数用来查询股票历史行情数据
条件： id, code, name 时间等
*/
// queryStockHisHq godoc
//
//	@Summary	queryStockHisHq
//	@Schemes
//	@Description	查询股票实时行情数据
//	@Tags			stock
//	@Param			code	query	string	false	"stock code"
//	@Param			start_date	query	string	false	"query start date"
//	@Param			end_date	query	string	false	"query end date"
// @Param period query string false "query period, day, week, month"
// @Param adjust query string false "query adjust, qfq, hfq, empty for no adjust"
// @Param order query int false "query order, order by createdate, 1 for asc, 0 for desc"
//	@Produce		json
//	@Success		200	{object}	mysql.StockHisHq
//	@Router			/v1/stock/queryStockHisHq [get]
func queryStockHisHq(c *gin.Context) {
	page := getPageDaoFromGin(c)
	apiCommonFuncWithPage(c, page, mdb.QueryStockHisHqByCond)
}

/*
insertStockHisHq...
此函数用来插入股票历史行情数据
*/
// insertStockHisHq godoc
//
//	@Summary	insertStockHisHq
//	@Schemes
//	@Description	insert stock history data
//	@Tags			stock
//	@Produce		json
//
//	@Param			in	body	mysql.StockHisHq	 true	"stock history data"
//
//	@Success		200			{object}	mysql.StockHisHq
//	@Router			/v1/stock/insertStockHisHq [post]
func insertStockHisHq(c *gin.Context) {
	apiCommonFunc(c, mdb.InsertStockHisHq)
}

/*
updateStockHisHq... 根据ID, code,name 更新股票历史行情数据
*/
// updateStockHisHq godoc
//
//	@Summary	updateStockHisHq
//	@Schemes
//	@Description	update stock history data
//	@Tags			stock
//	@Produce		json
//
//	@Param			in	body	mysql.StockHisHq	 true	"stock history data"
//
//	@Success		200			{object}	mysql.StockHisHq
//	@Router			/v1/stock/updateStockHisHq [post]
func updateStockHisHq(c *gin.Context) {
	apiCommonFunc(c, mdb.UpdateStockHisHq)
}
