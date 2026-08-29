/******************************************************************************
 * Author: liguoqiang
 * Date: 2024-05-10 20:02:41
 * LastEditors: liguoqiang
 * LastEditTime: 2024-05-26 15:34:53
 * Description: 股票指数数据操作系列接口
 * 用于实现股票指数基本信息(代码，名称，更新的时间等）的增删改查等操作
********************************************************************************/
package api

import (
	"rhserver/mdb"

	"github.com/gin-gonic/gin"
)

// queryIndexInfoByCode godoc
//
//	@Summary	queryIndexInfoByCode
//	@Schemes
//	@Description	查询股票指数基本信息，行情有哪些指数，指数的代码，名称，更新时间等
//	@Tags			stock index
//
//	@Param			code	query	string	true	"index code"
//
//	@Produce		json
//	@Success		200	{object}	mysql.IndexInfo
//	@Router			/v1/index/queryIndexInfoByCode [get]
func queryIndexInfoByCode(c *gin.Context) {
	apiCommonFunc(c, mdb.QueryStockIndexByCode)
}

/*******************************************************
* func queryAllIndexInfo(c *gin.Context)
* query IndexInfo
********************************************************/
// queryAllIndexInfo godoc
//
//	@Summary	queryAllIndexInfo
//	@Schemes
//	@Description	查询所有指数基本信息，行情有哪些指数，指数的代码，名称，更新时间等
//	@Tags			stock index
//
//	@Produce		json
//	@Success		200	{object}	mysql.IndexInfo
//	@Router			/v1/index/queryAllIndexInfo [get]
func queryAllIndexInfo(c *gin.Context) {
	//call  mdb.QueryAllIndexInfo() to query all index info
	//return status and result
	apiCommonFunc(c, mdb.QueryAllIndexInfo)
}

/*
此函数用来插入指数数据
*/
// insertIndex godoc
//
//	@Summary	insertIndex
//	@Schemes
//	@Description	insert stock index information
//	@Tags			stock index
//	@Produce		json
//
//	@Param			in	body	mysql.IndexInfo	 true	"index information"
//
//	@Success		200			{object}	mysql.IndexInfo
//	@Router			/v1/index/insertIndex [post]
func insertIndex(c *gin.Context) {
	apiCommonFunc(c, mdb.InsertStockIndex)
}

/*
updateIndex... 根据ID 更新指标
*/
// updateIndex godoc
//
//	@Summary	updateIndex
//	@Schemes
//	@Description	update stock index information
//	@Tags			stock index
//	@Produce		json
//
//	@Param			in	body	mysql.IndexInfo	 true	"index information"
//
//	@Success		200			{object}	mysql.IndexInfo
//	@Router			/v1/index/updateIndex [post]
func updateIndex(c *gin.Context) {
	apiCommonFunc(c, mdb.UpdateStockIndex)
}

/**********************************************************************************************
*
* 指数行情表接口, 用于实现股票指数行情数据操作
*
***********************************************************************************************/

/*
 */
// queryIndexHqByCode godoc
//
//	@Summary	queryIndexHqByCode
//	@Schemes
//	@Description	查询指数行情数据
//	@Tags			stock index
//
//	@Param			code	query	string	true	"index code"
// @Produce		json
// @Success		200	{object}	mongo.IndexHq
// @Router			/v1/index/queryIndexHqByCode [get]
func queryIndexHqByCode(c *gin.Context) {
	apiCommonFunc(c, mdb.QueryStockIndexHqByCode)
}

/*
此函数用来插入指数行情接口，用于WEB接口
*/
// insertIndexHq godoc
//
//	@Summary	insertIndexHq
//	@Schemes
//	@Description	insert stock index hq data
//	@Tags			stock index
//	@Produce		json
//
//	@Param			in	body	mongo.IndexHq	 true	"index information"
//
//	@Success		200			{object}	mongo.IndexHq
//	@Router			/v1/index/insertIndexHq [post]
func insertIndexHq(c *gin.Context) {
	apiCommonFunc(c, mdb.InsertStockIndexHq)
}

/*
updateIndexHq... 根据ID 更新指标行情数据
*/
// updateIndexHq godoc
//
//	@Summary	updateIndexHq
//	@Schemes
//	@Description	update stock index hq data
//	@Tags			stock index
//	@Produce		json
//
//	@Param			in	body	mongo.IndexHq	 true	"index hq"
//
//	@Success		200			{object}	mongo.IndexHq
//	@Router			/v1/index/updateIndexHq [post]
func updateIndexHq(c *gin.Context) {
	apiCommonFunc(c, mdb.UpdateStockIndexHq)
}

/*******************************************************
* func queryAllIndexCoStock(c *gin.Context)
* query IndexStock
********************************************************/
// queryAllIndexCoStock godoc
//
//	@Summary	queryAllIndexCoStock
//	@Schemes
//	@Description	查询指数成分股
//	@Tags			stock index
//
// @Produce		json
// @Success		200	{object}	mysql.IndexStock
// @Router			/v1/index/queryAllIndexCoStock [get]
func queryAllIndexCoStock(c *gin.Context) {
	//call  mdb.QueryAllIndexStock() to query all index stock
	//return status and result
	apiCommonFunc(c, mdb.QueryAllIndexCoStock)
}

/**
 * @function: queryIndexCoStockByCond
 * @description:
 * @return {*}
 */
// queryIndexCoStockByCond godoc
//
//	@Summary	queryIndexCoStockByCond
//	@Schemes
//	@Description	查询指数成分股
//	@Tags			stock index
//
//	@Param			index_code	query	string	true	"index code"
//	@Param			public_date	query	string	false	"publish date"
// @Produce		json
// @Success		200	{object}	mysql.IndexStock
// @Router			/v1/index/queryIndexCoStockByCond [get]
func queryIndexCoStockByCond(c *gin.Context) {
	apiCommonFunc(c, mdb.QueryIndexCoStockByCond)
}
