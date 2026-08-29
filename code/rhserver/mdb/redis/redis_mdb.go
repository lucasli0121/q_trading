/******************************************************************************
 * Author: liguoqiang
 * Date: 2023-04-08 14:42:44
 * LastEditors: liguoqiang
 * LastEditTime: 2024-06-18 19:17:45
 * Description:
********************************************************************************/
package redis

import (
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"os"
	"rhserver/cfg"
	mylog "rhserver/log"
	"time"

	"github.com/go-redis/redis"
)

var rdb *redis.Client = nil

/******************************************************************************
 * function: InitRedis
 * description: init redis, create redis client and set gopool
 * param {*gopool.Pool} taskPool
 * return {*}
********************************************************************************/
func InitRedis() bool {
	tsConfig := tls.Config{
		InsecureSkipVerify:       false,
		MinVersion:               tls.VersionTLS12,
		PreferServerCipherSuites: true,
	}
	cert, err := tls.LoadX509KeyPair(cfg.This.Redis.CertFile, cfg.This.Redis.KeyFile)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	tsConfig.Certificates = []tls.Certificate{cert}
	caPool := x509.NewCertPool()
	caPem, err := os.ReadFile(cfg.This.Redis.CaFile)
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	caPool.AppendCertsFromPEM(caPem)
	tsConfig.RootCAs = caPool
	rdb = redis.NewClient(&redis.Options{
		Addr:         cfg.This.Redis.Host,
		Password:     cfg.This.Redis.Password,
		DB:           cfg.This.Redis.Db,
		PoolSize:     100,
		MinIdleConns: 30,
		MaxRetries:   3,
		DialTimeout:  20 * time.Second,
		TLSConfig:    &tsConfig,
	})
	_, err = rdb.Ping().Result()
	if err != nil {
		mylog.Log.Errorln(err)
		return false
	}
	return true
}
func CloseRedis() {
	if rdb != nil {
		rdb.Close()
	}
}

/******************************************************************************
 * function: SaveValueToHash
 * description: 根据提供的key,field和value保存到redis的hash中
 * param key, field, timeout, v
 * return {*}
********************************************************************************/
func SaveValueToHash(key string, field string, timeout *time.Time, v interface{}) error {
	value, err := json.Marshal(v)
	if err != nil {
		mylog.Log.Errorln(err)
		return err
	}
	_, err = rdb.HSet(key, field, value).Result()
	if err != nil {
		mylog.Log.Errorln(err)
		return err
	} else {
		mylog.Log.Debugln("save redis HSet success, key:", key, "field:", field)
	}
	if timeout == nil {
		t := time.Now().AddDate(0, 0, 1) // default 1 天以后过期
		timeout = &t
	}
	rdb.ExpireAt(key, *timeout)
	return err
}

func GetValueFromHash(key string, field string, del bool, v interface{}) error {
	val, err := rdb.HGet(key, field).Result()
	if err != nil {
		mylog.Log.Errorln(err)
		return err
	}
	err = json.Unmarshal([]byte(val), v)
	if err != nil {
		mylog.Log.Errorln(err)
		return err
	}
	if del {
		rdb.HDel(key, field)
	}
	return err
}

/******************************************************************************
 * function: GetLValueFromList
 * description:
 * param {string} key
 * param {int64} index
 * param {bool} del
 * param {interface{}} v
 * return {*}
********************************************************************************/
func GetLValueFromList(key string, number int64, del bool) ([]string, error) {
	results, err := rdb.LRange(key, 0, number).Result()
	if err != nil {
		mylog.Log.Errorln(err)
		return nil, err
	}
	if del {
		len, err := rdb.LLen(key).Result()
		if err != nil {
			mylog.Log.Errorln(err)
		} else {
			if number >= 0 && number < len {
				rdb.LTrim(key, number+1, len)
			} else {
				rdb.LTrim(key, len, len)
			}
		}
	}
	return results, nil
}

func SetValueEx(key string, value string, exSeconds int) error {
	var tm time.Duration = time.Duration(exSeconds) * time.Second
	return rdb.Set(key, value, tm).Err()
}

func GetValue(key string) (string, error) {
	result := rdb.Get(key)
	return result.String(), result.Err()
}
