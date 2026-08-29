/*
 * @Author: liguoqiang
 * @Date: 2021-03-07 09:34:20
 * @LastEditors: liguoqiang
 * @LastEditTime: 2023-02-12 13:53:06
 * @Description: 实现 数据库的主函数, 连接mongodb
 */

package mongo

import (
	"context"
	"rhserver/cfg"
	mylog "rhserver/log"
	"rhserver/mdb/common"
	"time"

	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"
)

var mDb *mongo.Client = nil

func Open() bool {
	clientOptions := options.Client().ApplyURI(cfg.This.DB.Url)
	clientOptions.SetAuth(options.Credential{
		Username:   cfg.This.DB.Username,
		Password:   cfg.This.DB.Password,
		AuthSource: cfg.This.DB.Dbname,
	})
	ctx, _ := context.WithTimeout(context.TODO(), 10*time.Second)
	client, err := mongo.Connect(ctx, clientOptions)
	if err != nil {
		mylog.Log.Errorln("open database error:", err)
		return false
	}
	mDb = client
	return true
}
func Close() {
	ctx, _ := context.WithTimeout(context.TODO(), 10*time.Second)
	err := mDb.Disconnect(ctx)
	if err != nil {
		mylog.Log.Errorln(err)
	}
}

/********************************************************************
* 分页查询功能
* 通过limit, skip 实现简单分页
* pageNo==1时返回总页数
********************************************************************/
func QueryPage(table string, page *common.PageDao, filter interface{}, sort interface{}, cb func(*mongo.Cursor)) bool {
	tbl := mDb.Database(cfg.This.DB.Dbname).Collection(table)
	if filter == nil {
		filter = bson.M{}
	}
	totalPages := int64(0)
	opt := &options.FindOptions{}
	//if page.PageNo == 1 {
	totalCount, err := tbl.CountDocuments(context.TODO(), filter, &options.CountOptions{})
	if err != nil {
		mylog.Log.Errorln(err)
	}
	totalPages = int64(float32(totalCount)/float32(page.PageSize) + float32(0.5))
	//}
	opt.SetSort(sort).SetSkip(int64((page.PageNo - 1) * page.PageSize)).SetLimit(int64(page.PageSize))
	cur, err := tbl.Find(context.TODO(), filter, opt)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}

	defer func() {
		cur.Close(context.TODO())
	}()
	for cur.Next(context.TODO()) {
		cb(cur)
	}
	page.TotalPages = totalPages
	return true
}

/*
 * func Query, support method for any query
 *
 */
func QueryDao(table string, filter interface{}, cb func(*mongo.Cursor), opts ...*options.FindOptions) bool {
	tbl := mDb.Database(cfg.This.DB.Dbname).Collection(table)
	if filter == nil {
		filter = bson.M{}
	}
	cur, err := tbl.Find(context.TODO(), filter, opts...)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	defer func() {
		cur.Close(context.Background())
	}()
	for cur.Next(context.Background()) {
		cb(cur)
	}
	return true
}

// Find one by ID
func QueryDaoByID(table string, id primitive.ObjectID, obj Dao) bool {
	tbl := mDb.Database(cfg.This.DB.Dbname).Collection(table)
	res := tbl.FindOne(context.TODO(), bson.M{"_id": id})
	err := res.Decode(obj)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	return true
}

/*
* insert...
 */
func InsertDao(tblName string, obj Dao) bool {
	tbl := mDb.Database(cfg.This.DB.Dbname).Collection(tblName)
	res, err := tbl.InsertOne(context.TODO(), obj)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	id := res.InsertedID.(primitive.ObjectID)
	obj.SetID(id)
	return true
}

/*
* updateDaoById...
 */
func UpdateDaoByID(tblName string, id primitive.ObjectID, u bson.M) bool {
	tbl := mDb.Database(cfg.This.DB.Dbname).Collection(tblName)
	res, err := tbl.UpdateByID(context.TODO(), id, u)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	mylog.Log.Debugln("Modify count:", res.ModifiedCount)
	return true
}

/*
* deleteDaoByID...
 */
func DeleteDaoByID(tblName string, id primitive.ObjectID) bool {
	tbl := mDb.Database(cfg.This.DB.Dbname).Collection(tblName)
	filter := bson.M{"_id": id}
	res, err := tbl.DeleteOne(context.TODO(), filter)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	mylog.Log.Debugln("Delete count:", res.DeletedCount)
	return true
}
