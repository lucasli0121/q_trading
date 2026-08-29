/*
 * @Author: liguoqiang
 * @Date: 2021-07-23 16:25:27
 * @LastEditors: liguoqiang
 * @LastEditTime: 2023-04-08 14:45:43
 * @Description:
 */
package cfg

import (
	"fmt"
	"os"

	"gopkg.in/yaml.v2"
)

const (
	TmFmtStr        = "2006-01-02 15:04:05"
	DateFmtStr      = "2006-01-02"
	StaticPicPath   = "public/picture/"
	StaticVideoPath = "public/video/"
	StaticVoicePath = "public/voice/"
	StaticFilePath  = "public/file/"
)

type Cfg struct {
	Svr   SvrCfg   `yaml:"server"`
	DB    DbCfg    `yaml:"database"`
	Redis RedisCfg `yaml:"redis"`
	Mq    MqCfg    `yaml:"mq"`

	StaticPath string `yaml:"staticPath"`
	Log        LogCfg `yaml:"log"`
}

type SvrCfg struct {
	Host     string `yaml:"host"`
	OutUrl   string `yaml:"out_url"`
	CertFile string `yaml:"certFile"`
	KeyFile  string `yaml:"keyFile"`
	CaFile   string `yaml:"caFile"`
}
type DbCfg struct {
	Url      string `yaml:"url"`
	Username string `yaml:"username"`
	Password string `yaml:"password"`
	Dbname   string `yaml:"dbname"`
	Driver   string `yaml:"driver"`
}

type RedisCfg struct {
	Host     string `yaml:"host"`
	Password string `yaml:"password"`
	Db       int    `yaml:"db"`
	CertFile string `yaml:"certFile"`
	KeyFile  string `yaml:"keyFile"`
	CaFile   string `yaml:"caFile"`
}

type MqCfg struct {
	Host     string `yaml:"host"`
	Port     int    `yaml:"port"`
	ClientId string `yaml:"client_id"`
	Username string `yaml:"username"`
	Password string `yaml:"password"`
	CertFile string `yaml:"certFile"`
	KeyFile  string `yaml:"keyFile"`
	CaFile   string `yaml:"caFile"`
}

type LogCfg struct {
	Level      string `yaml:"level"`
	File       string `yaml:"file"`
	Maxsize    int    `yaml:"maxsize"`
	Maxage     int    `yaml:"maxage"`
	Maxbackups int    `yaml:"maxbackups"`
	Console    bool   `yaml:"console"`
	Format     string `yaml:"format"`
}

var This *Cfg = nil

func InitConfig(iniFile string) error {
	// _, fileName, _, _ := runtime.Caller(0)
	// filePath := path.Join(path.Dir(fileName), "cfg.yml")
	filePath := iniFile
	_, err := os.Stat(filePath)
	if err != nil {
		fmt.Printf("config file is not exist,%s", filePath)
		return err
	}
	yamlFile, err := os.ReadFile(filePath)
	if err != nil {
		fmt.Printf("ReadFile config error,%v", err)
		return err
	}
	This = new(Cfg)
	err = yaml.Unmarshal(yamlFile, This)
	if err != nil {
		fmt.Printf("yaml unmarshal error, %v", err)
		return err
	}
	fmt.Printf("initialize config successful")
	return nil
}

func IsMysql() bool {
	res := This.DB.Driver == "mysql"
	return res
}

func IsMongo() bool {
	return This.DB.Driver == "mongo"
}
