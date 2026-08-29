/******************************************************************************
 * Author: liguoqiang
 * Date: 2024-05-10 20:02:42
 * LastEditors: liguoqiang
 * LastEditTime: 2024-05-26 15:37:39
 * Description:定义数据库的开始操作入口，包括通用的操作接口
********************************************************************************/
package mdb

import (
	"rhserver/cfg"
	"rhserver/mdb/mongo"
	"rhserver/mdb/mysql"
)

/******************************************************
* 定义 数据库初始化函数
* 在open函数中实现数据库的打开操作
*******************************************************/

func Open() bool {
	if cfg.IsMongo() {
		return mongo.Open()
	} else if cfg.IsMysql() {
		return mysql.Open()
	}
	return false
}

func Close() {
	if cfg.IsMongo() {
		mongo.Close()
	} else if cfg.IsMysql() {
		mysql.Close()
	}
}
