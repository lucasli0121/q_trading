/*
 * @Author: liguoqiang
 * @Date: 2021-03-07 09:34:20
 * @LastEditors: liguoqiang
 * @LastEditTime: 2023-04-19 16:17:35
 * @Description: 实现 数据库的主函数, 连接mysql 操作
 */

package mysql

import (
	"bytes"
	"database/sql"
	"fmt"
	"math"
	"reflect"
	"rhserver/cfg"
	"rhserver/gopool"
	mylog "rhserver/log"
	"rhserver/mdb/common"
	"rhserver/mq"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	_ "github.com/go-sql-driver/mysql"
)

var mDb *sql.DB = nil
var taskPool *gopool.Pool = nil

/******************************************************************************
 * function:
 * description: MysqlDao... mysql所有数据对象的基类
 * return {*}
********************************************************************************/
type Dao interface {
	SetID(int64)
	QueryByID(int64) bool
	Insert() bool
	Update() bool
	Delete() bool
	DecodeFromGin(c *gin.Context)
	DecodeFromRow(row *sql.Row) error
	DecodeFromRows(rows *sql.Rows) error
}

/*
**********************************************************************************

	自定义一个float64类型，用来处理字符串和float转换时null或者nan的情况

**********************************************************************************
*/
type float64JSON float64

func (me *float64JSON) UnmarshalJSON(b []byte) error {
	b = bytes.Trim(b, "\"")
	strval := strings.ToLower(string(b))
	if strval == "nan" || strval == "null" {
		*me = 0.0
	} else {
		val, err := strconv.ParseFloat(strval, 64)
		if err != nil {
			return err
		}
		*me = float64JSON(val)
	}
	return nil
}

/******************************************************************************
 * function: Open
 * description: open mysql connection, must first run at main function
 * return {*}
********************************************************************************/
func Open() bool {
	dsn := cfg.This.DB.Username + ":" + cfg.This.DB.Password + "@" + cfg.This.DB.Url + "/" + cfg.This.DB.Dbname
	db, err := sql.Open("mysql", dsn)
	if err != nil {
		mylog.Log.Errorln("open mysql driver error:", err)
		return false
	}
	/* 连接数据库 */
	err = db.Ping()
	if err != nil {
		mylog.Log.Errorln("ping to mysql error:", err)
		return false
	}
	mDb = db
	mDb.SetConnMaxLifetime(time.Second * 120) // 每个连接最大存活时间
	mDb.SetConnMaxIdleTime(time.Second * 30)  // 每个连接最大空闲时间
	mDb.SetMaxIdleConns(500)                  // 最大空闲连接数
	mDb.SetMaxOpenConns(2048)                 // 连接池最大连接数
	// init task pool
	taskPool, _ = gopool.InitPool(512)
	subscribeStockTopic()
	return true
}

/******************************************************************************
 * function: Close
 * description: close mysql connection
 * return {*}
********************************************************************************/
func Close() {
	taskPool.Close()
	err := mDb.Close()
	if err != nil {
		mylog.Log.Errorln(err)
	}
}

/******************************************************************************
 * function: subscribeStockRealData
 * description: subscribe mq topic
 * return {*}
********************************************************************************/
func subscribeStockTopic() {
	mq.SubscribeTopic(common.REAL_TIME_NOTIFY_TOPIC, NewStockRealDataMQProc())
}

/********************************************************************
* 分页查询功能
* 通过limit, skip 实现简单分页
* pageNo==1时返回总页数
********************************************************************/
func QueryPage(table string, page *common.PageDao, filter interface{}, sort interface{}, cb func(*sql.Rows)) bool {
	totalPages := int64(0)
	sql := "select SQL_CALC_FOUND_ROWS * from " + table
	if filter != nil && len(filter.(string)) > 0 {
		sql += " where " + filter.(string)
	}
	if sort != nil && len(sort.(string)) > 0 {
		sql += " order by " + sort.(string)
	}
	sql += fmt.Sprintf(" limit %d offset %d", page.PageSize, page.PageNo-1)
	rows, err := mDb.Query(sql)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	defer rows.Close()
	for rows.Next() {
		cb(rows)
	}
	row := mDb.QueryRow("select FOUND_ROWS()")
	totalCount := int64(0)
	if row != nil {
		row.Scan(&totalCount)
	}
	totalPages = int64(float32(totalCount)/float32(page.PageSize) + float32(0.5))
	page.TotalPages = totalPages
	return true
}

/*
 * func Query, support method for any query
 *
 */
func QueryDao(table string, filter interface{}, sort interface{}, limited interface{}, cb func(*sql.Rows)) bool {
	sql := "select * from " + table
	if filter != nil && len(filter.(string)) > 0 {
		sql += " where " + filter.(string)
	}
	if sort != nil && len(sort.(string)) > 0 {
		sql += " order by " + sort.(string)
	}
	if limited != nil && len(limited.(string)) > 0 {
		sql += " limit " + limited.(string)
	}
	rows, err := mDb.Query(sql)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	defer rows.Close()
	for rows.Next() {
		cb(rows)
	}
	return true
}

// Find one by ID
func QueryDaoByID(table string, id int64, obj Dao) bool {
	sql := "select * from " + table + " where id=?"
	row := mDb.QueryRow(sql, id)
	err := obj.DecodeFromRow(row)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	return true
}

func CheckTableExist(tblName string) bool {
	sql := fmt.Sprintf("show tables like '%%%s%%'", tblName)
	rows, err := mDb.Query(sql)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	defer rows.Close()
	var table string
	for rows.Next() {
		err := rows.Scan(&table)
		if err != nil {
			mylog.Log.Errorln(err)
		} else if strings.EqualFold(table, tblName) {
			return true
		}
	}
	return false
}

func CreateTable(sql string) error {
	_, err := mDb.Exec(sql)
	return err
}

/******************************************************************************
 * function: CreateTableWithDao
 * description:  create table with struct
 *	using reflect to get struct field and value
 *  and then generate sql(mysql format) to create table
 *  Tag: mysql, common, binding, size, isnull, default, unique, key
 *  default id is primary key, other key field should be set key tag
 * param {string} tblName
 * param {Dao} obj
 * return {*}
********************************************************************************/
func CreateTableWithDao(tblName string, obj Dao) bool {
	sql := fmt.Sprintf(`create table if not exists %s (`, tblName)
	var fields string
	var keys string = "primary key("
	var unique string
	u := reflect.TypeOf(obj)
	numField := u.Elem().NumField()
	for num := 0; num < numField; num++ {
		f := u.Elem().Field(num)
		if len(fields) > 0 {
			fields += `,`
		}
		tag := f.Tag.Get("mysql")
		common := f.Tag.Get("common")
		fields += tag
		if tag == "id" {
			fields += " MEDIUMINT not null auto_increment "
			keys += "id"
		} else if f.Type.String() == "time.Time" {
			fields += " datetime"
		} else {
			switch f.Type.Kind() {
			case reflect.Int:
				fields += " int"
			case reflect.Int64:
				fields += " bigint"
			case reflect.Float32:
				fields += " float"
			case reflect.Float64:
				fields += " double"
			case reflect.String:
				binding := f.Tag.Get("binding")
				if len(binding) > 0 && strings.Split(binding, "=")[0] == "datetime" {
					if len(strings.Split(binding, "=")) > 1 && len(strings.Split(binding, "=")[1]) <= 11 {
						fields += " date"
					} else {
						fields += " datetime"
					}
				} else {
					if f.Tag.Get("size") != "" {
						fields += " varchar(" + f.Tag.Get("size") + ")"
					} else {
						fields += " varchar(255)"
					}
				}
			case reflect.Pointer:
				if f.Type.Elem().Kind() == reflect.String {
					binding := f.Tag.Get("binding")
					if len(binding) > 0 && strings.Split(binding, "=")[0] == "datetime" {
						if len(strings.Split(binding, "=")) > 1 && len(strings.Split(binding, "=")[1]) <= 11 {
							fields += " date"
						} else {
							fields += " datetime"
						}
					} else {
						if f.Tag.Get("size") != "" {
							fields += " varchar(" + f.Tag.Get("size") + ")"
						} else {
							fields += " varchar(255)"
						}
					}
				}
			}
			if f.Tag.Get("isnull") == "false" || f.Tag.Get("binding") == "required" {
				fields += " not null"
			}
			if f.Tag.Get("default") != "" {
				fields += " default " + f.Tag.Get("default")
			}
			if f.Tag.Get("unique") == "true" {
				if len(unique) > 0 {
					unique += ","
				}
				unique += tag
			}
			if f.Tag.Get("key") == "true" {
				keys += "," + tag
			}
		}
		if len(common) > 0 {
			fields += " comment '" + common + "'"
		}
	}
	sql += fields + "," + keys + ")"
	if len(unique) > 0 {
		sql += ", constraint " + tblName + "_unique unique(" + unique + ")"
	}
	sql += ") DEFAULT CHARSET=utf8;"
	_, err := mDb.Exec(sql)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	return true
}

/******************************************************************************
 * function: InsertDao
 * description: insert new record to table
 *	using reflect to get struct field and value
 *  and then generate sql(mysql format) to insert
 * param {string} tblName
 * param {Dao} obj
 * return {*}
********************************************************************************/
func InsertDao(tblName string, obj Dao) bool {
	sql := fmt.Sprintf("insert into %s ", tblName)
	u := reflect.TypeOf(obj)
	vf := reflect.ValueOf(obj)
	var fields string
	var values string
	numField := u.Elem().NumField()
	for num := 0; num < numField; num++ {
		f := u.Elem().Field(num)
		v := vf.Elem().Field(num)
		if f.Tag.Get("mysql") == "id" {
			continue
		}
		if len(fields) > 0 {
			fields += ","
		}
		if len(values) > 0 {
			values += ","
		}
		fields += f.Tag.Get("mysql")
		switch v.Kind() {
		case reflect.Int64:
			values += fmt.Sprintf("%d", v.Int())
		case reflect.Int:
			values += fmt.Sprintf("%d", v.Int())
		case reflect.Float64:
			if math.IsNaN(v.Float()) {
				values += "NULL"
			} else {
				values += fmt.Sprintf("%v", v.Float())
			}
		case reflect.String:
			values += "'" + v.String() + "'"
		case reflect.Pointer:
			if v.IsNil() {
				values += "NULL"
			} else {
				if f.Type.Elem().Kind() == reflect.String {
					values += "'" + v.Elem().String() + "'"
				}
			}
		}
	}
	sql += fmt.Sprintf(" (%s) values (%s)", fields, values)
	result, err := mDb.Exec(sql)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	id, err := result.LastInsertId()
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	obj.SetID(id)
	return true
}

/******************************************************************************
 * function: UpdateDaoByID
 * description: update table record by id
 * return {*}
********************************************************************************/
func UpdateDaoByID(tblName string, id int64, obj Dao) bool {
	sql := fmt.Sprintf("update %s ", tblName)
	u := reflect.TypeOf(obj)
	vf := reflect.ValueOf(obj)
	var setsql string
	numField := u.Elem().NumField()
	for num := 0; num < numField; num++ {
		f := u.Elem().Field(num)
		v := vf.Elem().Field(num)
		if f.Tag.Get("mysql") == "id" {
			continue
		}
		var setval string
		setval = fmt.Sprintf(" %s=", f.Tag.Get("mysql"))
		switch v.Kind() {
		case reflect.Int64:
			setval += fmt.Sprintf("%d", v.Int())
		case reflect.Float64:
			if math.IsNaN(v.Float()) {
				setval += "NULL"
			} else {
				setval += fmt.Sprintf("%v", v.Float())
			}
		case reflect.Int:
			setval += fmt.Sprintf("%d", v.Int())
		case reflect.String:
			setval += "'" + v.String() + "'"
		case reflect.Pointer:
			if v.IsNil() {
				setval += "NULL"
			} else {
				if f.Type.Elem().Kind() == reflect.String {
					setval += "'" + v.Elem().String() + "'"
				}
			}
		}
		if len(setsql) > 0 {
			setsql += "," + setval
		} else {
			setsql = setval
		}
	}
	sql += fmt.Sprintf(" set %s where id=%d", setsql, id)
	result, err := mDb.Exec(sql)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	count, err := result.RowsAffected()
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	mylog.Log.Debugln("Update table:", tblName, ", and affected rows:", count)
	return true
}

/******************************************************************************
 * function: UpdateDaoByFilter
 * description: update table record using condition
 * param {string} tblName
 * param {string} filter condition
 * param {Dao} obj
 * return {*}
********************************************************************************/
func UpdateDaoByFilter(tblName string, filter string, obj Dao) bool {
	if len(filter) == 0 {
		return false
	}
	sql := fmt.Sprintf("update %s ", tblName)
	u := reflect.TypeOf(obj)
	vf := reflect.ValueOf(obj)
	var setsql string
	numField := u.Elem().NumField()
	for num := 0; num < numField; num++ {
		f := u.Elem().Field(num)
		v := vf.Elem().Field(num)
		if f.Tag.Get("mysql") == "id" {
			continue
		}
		var setval string
		setval = fmt.Sprintf(" %s=", f.Tag.Get("mysql"))
		switch v.Kind() {
		case reflect.Int64:
			setval += fmt.Sprintf("%d", v.Int())
		case reflect.Float64:
			if math.IsNaN(v.Float()) {
				setval += "NULL"
			} else {
				setval += fmt.Sprintf("%v", v.Float())
			}
		case reflect.Int:
			setval += fmt.Sprintf("%d", v.Int())
		case reflect.String:
			setval += "'" + v.String() + "'"
		case reflect.Pointer:
			if v.IsNil() {
				setval += "NULL"
			} else {
				if f.Type.Elem().Kind() == reflect.String {
					setval += "'" + v.Elem().String() + "'"
				}
			}
		}
		if len(setsql) > 0 {
			setsql += "," + setval
		} else {
			setsql = setval
		}
	}
	sql += fmt.Sprintf(" set %s where %s", setsql, filter)
	result, err := mDb.Exec(sql)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	count, err := result.RowsAffected()
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	mylog.Log.Debugln("Update table:", tblName, ", and affected rows:", count)
	return true
}

/*
* deleteDaoByID...
 */
func DeleteDaoByID(tblName string, id int64) bool {
	sql := fmt.Sprintf("delete from %s where id=%d", tblName, id)
	result, err := mDb.Exec(sql)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	count, err := result.RowsAffected()
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	mylog.Log.Debugln("Delete table:", tblName, " count:", count)
	return true
}

/******************************************************************************
 * function: DeleteDaoByFilter
 * description: delete record using condition
 * return {*}
********************************************************************************/
func DeleteDaoByFilter(tblName string, filter string) bool {
	sql := fmt.Sprintf("delete from %s where %s", tblName, filter)
	result, err := mDb.Exec(sql)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	count, err := result.RowsAffected()
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	mylog.Log.Debugln("Delete table:", tblName, " count:", count)
	return true
}
