/******************************************************************************
 * Author: liguoqiang
 * Date: 2024-04-01 13:37:10
 * LastEditors: liguoqiang
 * LastEditTime: 2024-05-29 08:18:53
 * Description:
********************************************************************************/
/*
 * @Author: liguoqiang
 * @Date: 2021-03-07 09:31:25
 * @LastEditors: liguoqiang
 * @LastEditTime: 2023-04-17 21:29:47
 * @Description: 实现股票后台管理的主程序
 */
package main

import (
	"fmt"
	"rhserver/api"
	"rhserver/cfg"
	mylog "rhserver/log"
	"rhserver/mdb"
	"rhserver/mdb/redis"
	"rhserver/mq"
)

func main() {
	err := cfg.InitConfig("./cfg/cfg.yml")
	if err != nil {
		fmt.Println("initialize config failed, ", err)
		return
	}
	mylog.Init()
	defer mylog.Close()
	//启动redis
	if !redis.InitRedis() {
		fmt.Println("init redis failed exit!")
		return
	}
	defer redis.CloseRedis()
	//init mqtt object
	if !mq.InitMqtt() {
		fmt.Println("init mqtt failed exit!")
		return
	}
	defer mq.CloseMqtt()
	// database
	if !mdb.Open() {
		fmt.Println("connect database failed exit!")
		return
	}

	defer mdb.Close()
	//启动web服务
	api.StartWeb()
}
