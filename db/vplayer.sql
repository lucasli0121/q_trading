drop database if EXISTS vplayer;
create database vplayer;
use vplayer;

create user 'vplayer'@'%' identified by 'vplayer@123';
grant all privileges on vplayer.* to 'vplayer'@'%';
flush privileges;
    
set names utf8;

DROP TABLE IF EXISTS `stock_info_tbl`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
/* 定义股票基本信息的表 */
CREATE TABLE `stock_info_tbl` (
  `id` MEDIUMINT NOT NULL AUTO_INCREMENT,
  `code` char(32) NOT NULL COMMENT '代码',
  `name` varchar(32) NOT NULL COMMENT '名称',
  `industry` varchar(64) default NULL COMMENT '所属行业',
  `total_shares` float DEFAULT 0.0 COMMENT '总股本',
  `circul_shares` float DEFAULT 0.0 COMMENT '流通股',
  `total_cap` float DEFAULT 0.0 COMMENT '总市值',
  `circul_cap` float DEFAULT 0.0 COMMENT '流通市值',
  `market_date` date  COMMENT '上市时间',
  `dayhq_update_date` date comment '上次更新日行情的时间',
  `weekhq_update_date` date comment '上次更新周行情的时间',
  `monthhq_update_date` date comment '上次更新月行情的时间',
  PRIMARY KEY (`id`,`code`)
)ENGINE=InnoDB DEFAULT CHARSET=utf8;



-- 创建指数行情表

DROP TABLE IF EXISTS `index_info`;
create table index_info (
	id MEDIUMINT NOT NULL AUTO_INCREMENT,
    code char(32) not null comment '代码',
    name varchar(64) NOT NULL COMMENT '名称',
    pulish_date date default null comment '指数发布日期',
    update_date date default null comment '更新日期',
    primary key(id, code)
)ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- 创建指数成分股

DROP TABLE IF EXISTS `index_co_stocks`;
create table index_co_stocks (
	id MEDIUMINT NOT NULL AUTO_INCREMENT,
    index_code char(32) not null comment '指数代码',
    code char(32) not null comment '代码',
    name varchar(32) NOT NULL COMMENT '名称',
    in_date date default null comment '纳入日期',
    update_date date default null comment '更新日期',
    PRIMARY KEY (`id`,index_code,`code`)
)ENGINE=InnoDB DEFAULT CHARSET=utf8;






