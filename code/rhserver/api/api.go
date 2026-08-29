/*
 * @Author: liguoqiang
 * @Date: 2022-06-02 17:04:32
 * @LastEditors: liguoqiang
 * @LastEditTime: 2023-05-01 23:01:52
 * @Description:
 */
package api

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"time"

	"rhserver/cfg"
	"rhserver/exception"
	"rhserver/mdb/common"

	_ "rhserver/docs"
	mylog "rhserver/log"

	"github.com/didip/tollbooth"
	"github.com/didip/tollbooth_gin"
	"github.com/gin-gonic/gin"
	swaggerFiles "github.com/swaggo/files"
	ginSwagger "github.com/swaggo/gin-swagger"
)

var postAction map[string]gin.HandlerFunc
var getAction map[string]gin.HandlerFunc
var svcHttp *http.Server = nil

// StartWeb function run a webservice at webPort
func StartWeb() {
	// 设置限流
	limt := tollbooth.NewLimiter(100, nil)
	limt.SetIPLookups([]string{"RemoteAddr", "X-Forwarded-For", "X-Real-IP"}).SetMethods([]string{"GET", "POST"})
	limt.SetMessage("{ \"code\": 201, \"message\": \"reached max request limit\"}")
	router := gin.Default()
	v1 := router.Group("/v1")
	initActions()
	for k, v := range getAction {
		v1.GET(k, tollbooth_gin.LimitHandler(limt), v)
	}
	for k, v := range postAction {
		v1.POST(k, tollbooth_gin.LimitHandler(limt), v)
	}
	router.MaxMultipartMemory = 8 << 40
	router.Static("/public", cfg.This.StaticPath)
	router.GET("/swagger/*any", ginSwagger.WrapHandler(swaggerFiles.Handler))
	// config TLS config
	tsConfig := tls.Config{
		InsecureSkipVerify:       false,
		MinVersion:               tls.VersionTLS12,
		PreferServerCipherSuites: true,
	}
	cert, err := tls.LoadX509KeyPair(cfg.This.Svr.CertFile, cfg.This.Svr.KeyFile)
	if err != nil {
		mylog.Log.Errorln(err)
		return
	}
	tsConfig.Certificates = []tls.Certificate{cert}
	caPool := x509.NewCertPool()
	caPem, err := os.ReadFile(cfg.This.Svr.CaFile)
	if err != nil {
		mylog.Log.Errorln(err)
		return
	}
	caPool.AppendCertsFromPEM(caPem)
	tsConfig.RootCAs = caPool
	// 单独启动http server，用于后面的关闭操作
	svcHttp = &http.Server{
		Addr:         cfg.This.Svr.Host,
		Handler:      router,
		ReadTimeout:  60 * time.Second,
		WriteTimeout: 60 * time.Second,
		TLSConfig:    &tsConfig,
	}
	// 启动一个go例程用于启动服务
	go func() {
		err := svcHttp.ListenAndServeTLS("", "")
		if err != nil {
			mylog.Log.Errorf("start web server failed, %s", cfg.This.Svr.Host)
		}
	}()
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, os.Interrupt, os.Kill)
	<-quit

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	svcHttp.Shutdown(ctx)
	<-ctx.Done()
	mylog.Log.Info("Shutdowning is done!!")
}

func initActions() {
	getAction = make(map[string]gin.HandlerFunc)
	getAction["/index/queryIndexInfoByCode"] = queryIndexInfoByCode
	getAction["/index/queryAllIndexInfo"] = queryAllIndexInfo
	getAction["/index/queryIndexHqByCode"] = queryIndexHqByCode
	getAction["/index/queryAllIndexCoStock"] = queryAllIndexCoStock
	getAction["/index/queryIndexCoStockByCond"] = queryIndexCoStockByCond
	getAction["/stock/queryStockInfoByCode"] = queryStockInfoByCode
	getAction["/stock/queryRealTimeHqByCode"] = queryRealTimeHqByCode
	getAction["/stock/queryAllLatestRealTimeHq"] = queryAllLatestRealTimeHq
	getAction["/stock/queryStockHisHq"] = queryStockHisHq

	postAction = make(map[string]gin.HandlerFunc)
	postAction["/index/insertIndex"] = insertIndex
	postAction["/index/updateIndex"] = updateIndex
	postAction["/index/insertIndexHq"] = insertIndexHq
	postAction["/index/updateIndexHq"] = updateIndexHq
	postAction["/stock/insertStockInfo"] = insertStockInfo
	postAction["/stock/updateStockInfo"] = updateStockInfo
	postAction["/stock/insertRealTimeHq"] = insertRealTimeHq
	postAction["/stock/updateRealTimeHq"] = updateRealTimeHq
	postAction["/stock/insertStockHisHq"] = insertStockHisHq
	postAction["/stock/updateStockHisHq"] = updateStockHisHq

	postAction["/upload/picture"] = uploadPicFun
	postAction["/upload/video"] = uploadVideoFun
	postAction["/upload/voice"] = uploadVoiceFun
	postAction["/upload/file"] = uploadFileFun
}

/******************************************************************************
 * function: getPageDaoFromGin
 * description: A universal function for getting page params from gin.Context
 * param {*gin.Context} c
 * return {*}
********************************************************************************/
func getPageDaoFromGin(c *gin.Context) *common.PageDao {
	pageNo := c.Query("pageNo")
	pageSize := c.Query("pageSize")
	var page *common.PageDao = nil
	if pageNo != "" {
		no, err := strconv.ParseInt(pageNo, 10, 64)
		if err != nil {
			return nil
		}
		size := common.DEFAULT_PAGE_SIZE
		if pageSize != "" {
			size, err = strconv.ParseInt(pageSize, 10, 64)
			if err != nil {
				return nil
			}
		}
		page = common.NewPageDao(no, size)
	}
	return page
}

/*
上传图片接口
*/
func uploadPicFun(c *gin.Context) {
	uploadFileFunc(c, cfg.StaticPicPath)
}

/*
上传文件接口
*/
func uploadFileFun(c *gin.Context) {
	uploadFileFunc(c, cfg.StaticFilePath)
}

/*
上传视频文件接口
*/
func uploadVideoFun(c *gin.Context) {
	uploadFileFunc(c, cfg.StaticVideoPath)
}

/*
上传音频文件
*/
func uploadVoiceFun(c *gin.Context) {
	uploadFileFunc(c, cfg.StaticVoicePath)
}

func uploadFileFunc(c *gin.Context, staticPath string) {
	exception.TryEx{
		Try: func() {
			file, err := c.FormFile("file")
			if err != nil {
				exception.Throw(common.ParamError, "not found 'file' field in form data")
			}
			filename := staticPath + filepath.Base(file.Filename)
			if err := c.SaveUploadedFile(file, filename); err != nil {
				exception.Throw(common.UploadError, "save file or upload failed")
			}
			respJSON(c, common.Success, cfg.This.Svr.OutUrl+filename)
		},
		Catch: func(e exception.Exception) {
			respJSON(c, e.Code, e.Msg)
		},
	}.Run()
}

// 返回response http 回应函数，返回为json，格式为
// 错误信息：{ code: 201, message: "" }，正常信息： {code: 200, data: {} }
func respJSON(c *gin.Context, status int, msg interface{}) {
	if status != http.StatusOK {
		c.JSON(status, gin.H{"code": status, "message": msg})
	} else {
		c.JSON(status, gin.H{"code": status, "data": msg})
	}
}

// 返回带页号的response 回应，格式为{code: 200, pageNo: 1, pageSize 20, data: {} }
func respJSONWithPage(c *gin.Context, status int, page *common.PageDao, msg interface{}) {
	if status != http.StatusOK {
		c.JSON(status, gin.H{"code": status, "message": msg})
	} else {
		c.JSON(status, gin.H{"code": status, "pageNo": page.PageNo, "pageSize": page.PageSize, "totalPage": page.TotalPages, "data": msg})
	}
}

/******************************************************************************
 * function: apiCommonFunc
 * description: define a common function for api
 * param {*gin.Context} c
 * return {*}
********************************************************************************/
func apiCommonFunc(c *gin.Context, mdbFunc func(c *gin.Context) (int, interface{})) {
	exception.TryEx{
		Try: func() {
			status, result := mdbFunc(c)
			respJSON(c, status, result)
		},
		Catch: func(e exception.Exception) {
			respJSON(c, common.OtherError, e.Msg)
		},
	}.Run()
}

/******************************************************************************
 * function: apiCommonFuncWithPage
 * description: common function with page
 * param {*gin.Context} c
 * param {*common.PageDao} page
 * return {*}
********************************************************************************/
func apiCommonFuncWithPage(c *gin.Context, page *common.PageDao, mdbFunc func(c *gin.Context, page *common.PageDao) (int, interface{})) {
	exception.TryEx{
		Try: func() {
			status, result := mdbFunc(c, page)
			respJSONWithPage(c, status, page, result)
		},
		Catch: func(e exception.Exception) {
			respJSON(c, common.OtherError, e.Msg)
		},
	}.Run()
}
