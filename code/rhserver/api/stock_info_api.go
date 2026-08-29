/******************************************************************************
 * Author: liguoqiang
 * Date: 2024-05-10 20:02:41
 * LastEditors: liguoqiang
 * LastEditTime: 2024-05-26 21:03:27
 * Description:
********************************************************************************/
/*********************************************************************
* 此文件实现股票基本信息的操作，增删改查
* 股票信息包括：代码，名称，市值，流通，所属行业，指数等
* 如果需要查询实时数据或者历史数据，可能需要先查询此接口，因为此接口可以查询到所有股票基本资料
**********************************************************************/

package api

import (
	"rhserver/mdb"

	"github.com/gin-gonic/gin"
)

/*
WEB 接口，此函数用来查询股票信息
条件： id, code, name
*/
// queryStockInfoByCode godoc
//
//	@Summary	queryStockInfoByCode
//	@Schemes
//	@Description	根据code查询股票基本信息，股票基本信息包含股票代码，名称，市值，流通，所属行业等
//	@Tags			stock
//
//	@Param			code	query	string	false	"stock code"
//
//	@Produce		json
//	@Success		200	{object}	mysql.StockInfo
//	@Router			/v1/stock/queryStockInfoByCode [get]
func queryStockInfoByCode(c *gin.Context) {
	page := getPageDaoFromGin(c)
	apiCommonFuncWithPage(c, page, mdb.QueryStockInfoByCond)
}

/*
insertStockInfo...
此函数用来插入股票基本数据
*/
// insertStockInfo godoc
//
//	@Summary	insertStockInfo
//	@Schemes
//	@Description	insert stock information
//	@Tags			stock
//	@Produce		json
//
//	@Param			in	body	mysql.StockInfo	 true	"stock information"
//
//	@Success		200			{object}	mysql.StockInfo
//	@Router			/v1/stock/insertStockInfo [post]
func insertStockInfo(c *gin.Context) {
	apiCommonFunc(c, mdb.InsertStockInfo)
}

/*
updateStockInfo... 根据ID, code,name 更新股票基本数据
*/
// updateStockInfo godoc
//
//	@Summary	updateStockInfo
//	@Schemes
//	@Description	update stock information
//	@Tags			stock
//	@Produce		json
//
//	@Param			in	body	mysql.StockInfo	 true	"stock information"
//
//	@Success		200			{object}	mysql.StockInfo
//	@Router			/v1/stock/updateStockInfo [post]
func updateStockInfo(c *gin.Context) {
	apiCommonFunc(c, mdb.UpdateStockInfo)
}
