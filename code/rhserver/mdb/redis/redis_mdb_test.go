/*
 * @Author: liguoqiang
 * @Date: 2023-04-30 23:31:28
 * @LastEditors: liguoqiang
 * @LastEditTime: 2023-05-01 21:01:27
 * @Description:
 */
package redis

import (
	"rhserver/cfg"
	"testing"
)

func TestSaveValueToHash(t *testing.T) {
	err := cfg.InitConfig("../../cfg/cfg.yml")
	if err != nil {
		t.Error("initialize config failed, ", err)
		return
	}
	if !InitRedis() {
		t.Error("init redis failed")
		return
	}
	defer CloseRedis()
	type TestValue struct {
		V1 string `json:"v1"`
		V2 string `json:"v2"`
	}
	testVal := &TestValue{
		V1: "test1",
		V2: "test2",
	}
	err = SaveValueToHash("key", "2024-5-25", nil, testVal)
	if err != nil {
		t.Error(err)
		return
	}
}

func TestGetValueFromHash(t *testing.T) {
	err := cfg.InitConfig("../../cfg/cfg.yml")
	if err != nil {
		t.Error("initialize config failed, ", err)
		return
	}
	if !InitRedis() {
		t.Error("init redis failed")
		return
	}
	defer CloseRedis()
	type TestValue struct {
		V1 string `json:"v1"`
		V2 string `json:"v2"`
	}

	var val TestValue
	err = GetValueFromHash("key", "2024-5-25", true, &val)
	if err != nil {
		t.Error(err)
		return
	}
	if val.V1 != "test1" || val.V2 != "test2" {
		t.Error("get value from hash failed")
	}
}
