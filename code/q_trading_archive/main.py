"""q_trading_archive 入口。

独立进程，两种运行方式：

    python main.py                 # 跑一次就退出（可挂 cron / 计划任务）
    python main.py --daemon        # 常驻，每日收盘后自动归档

它是只读 mongo 的旁路进程：不写任何库、不连 redis、不订阅 mqtt。
放在 q_trading_work 之外是刻意的：q_trading_work 可多实例部署，
归档逻辑塞进去会被每个实例各跑一遍，重复写同一份数据。
"""

from __future__ import annotations

import argparse
import importlib
import logging
import logging.config
import sys
import tempfile
from pathlib import Path

import yaml

from archive.archiver import ArchiveReport, Archiver
from archive.config import (
    APP_ROOT,
    ArchiveConfig,
    ConfigError,
    candidate_cfg_paths,
    load_config,
)
from archive.mongo_reader import MongoReader
from archive.state import ArchiveState
from archive.store import ParquetStore

LOGGER_NAME = "archive"
TIMEZONE = "Asia/Shanghai"


def setup_logging(verbose: bool = False) -> None:
    """初始化日志：控制台 + 文件两道闸门。

    log.yaml 与 stock.cfg 一样按「包根 → _MEIPASS」查找。冻结态下它随包落在
    `_internal/cfg/`，只在包根找会读不到，然后静默降级为仅控制台输出：
    进程照常运行、日志照常打，只是 `log/archive.log` 永不生成，文件轮转与
    DEBUG 级别一并失效。这类失效没有任何报错，只能靠代码堵住并留下警告。
    """
    (APP_ROOT / "log").mkdir(parents=True, exist_ok=True)

    cfg_path = next(
        (path for path in candidate_cfg_paths("log.yaml") if path.is_file()),
        None,
    )

    if cfg_path is None:
        logging.basicConfig(
            level=logging.DEBUG if verbose else logging.INFO,
            format="%(asctime)s|%(levelname)s|%(message)s",
        )
        logging.getLogger(LOGGER_NAME).warning(
            "未找到 log.yaml，已退化为仅控制台输出。查找过: %s",
            ", ".join(str(path) for path in candidate_cfg_paths("log.yaml")),
        )
        return

    with cfg_path.open("r", encoding="utf-8") as fh:
        conf = yaml.safe_load(fh) or {}

    # dictConfig 里的相对路径以 CWD 为基准，这里统一换算成绝对路径，
    # 否则从别的目录启动会把日志写到意料之外的位置。
    for handler in (conf.get("handlers") or {}).values():
        filename = handler.get("filename") if isinstance(handler, dict) else None
        if filename:
            path = Path(filename)
            if not path.is_absolute():
                handler["filename"] = str(APP_ROOT / path)

    logging.config.dictConfig(conf)
    if verbose:
        logging.getLogger(LOGGER_NAME).setLevel(logging.DEBUG)


def self_check() -> int:
    """自检运行环境，不连数据库、不写归档产物。

    存在的理由：打包产物最典型的失效是「某个模块没被收集进来」，
    而这类问题只在真正调用到那一步才暴露。出现在盘后定时任务里，
    比出现在构建日志里代价大得多。这里把重依赖逐个导入，
    并实地走一遍 parquet 的写与读，让构建阶段与部署后都能一次问清。
    """
    print("运行环境自检")
    print("  Python      : %s" % sys.version.split()[0])
    print("  冻结运行     : %s" % bool(getattr(sys, "frozen", False)))
    print("  部署根       : %s" % APP_ROOT)
    print("  可执行文件   : %s" % sys.executable)

    ok = True
    for name in ("pandas", "pyarrow", "pymongo", "apscheduler", "yaml", "pytz"):
        try:
            mod = importlib.import_module(name)
            print("  %-11s : %s" % (name, getattr(mod, "__version__", "?")))
        except Exception as exc:  # noqa: BLE001 - 自检要报出全部失败项，不中断
            ok = False
            print("  %-11s : 导入失败 %s" % (name, exc))

    # 配置候选逐个列出但不判定失败：构建环境本来就没有真实凭据文件，
    # 缺它属于预期，部署时补齐即可。
    for path in candidate_cfg_paths():
        print("  配置候选     : %s（%s）" % (path, "存在" if path.is_file() else "缺失"))

    try:
        import pandas as pd

        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.parquet"
            source = pd.DataFrame({"code": ["000001.SZ"], "close": [1.23]})
            source.to_parquet(probe, engine="pyarrow", index=False, compression="zstd")
            loaded = pd.read_parquet(probe, engine="pyarrow")
            if loaded.shape != source.shape or abs(float(loaded["close"].iloc[0]) - 1.23) > 1e-9:
                ok = False
                print("  parquet 写读 : 数据不一致")
            else:
                print("  parquet 写读 : 正常（pyarrow + zstd）")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print("  parquet 写读 : 失败 %s" % exc)

    print("自检通过" if ok else "自检未通过")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="q_trading_archive",
        description="把服务端滚动窗口内的行情按日转存为本地 parquet，长期留存。",
    )
    parser.add_argument("--config", default=None, help="配置文件路径，默认 cfg/stock.cfg")
    parser.add_argument("--daemon", action="store_true", help="常驻运行，按配置时间每日归档")
    parser.add_argument("--start", "--from", dest="start", default=None,
                        help="起始日期 YYYY-MM-DD（含）")
    parser.add_argument("--end", "--to", dest="end", default=None,
                        help="结束日期 YYYY-MM-DD（含）")
    parser.add_argument("--refresh-days", type=int, default=None,
                        help="额外重刷最近 N 个自然日，覆盖盘中写入的半截数据")
    parser.add_argument("--full-refresh", action="store_true",
                        help="忽略进度，从库里最早日期开始回溯")
    parser.add_argument("--dry-run", action="store_true", help="只列出将要归档的日期")
    parser.add_argument("--self-check", action="store_true",
                        help="检查运行环境（依赖、路径、parquet 读写），不连数据库")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    return parser


def build_archiver(cfg: ArchiveConfig) -> tuple[Archiver, MongoReader]:
    reader = MongoReader(cfg.mongo, batch_size=cfg.batch_size)
    store = ParquetStore(cfg.data_dir)
    state = ArchiveState(cfg.data_dir.parent / ".state.json")
    return Archiver(cfg, reader, store, state), reader


def run_once(cfg: ArchiveConfig, args: argparse.Namespace) -> ArchiveReport:
    archiver, reader = build_archiver(cfg)
    logger = logging.getLogger(LOGGER_NAME)
    try:
        if not reader.ping():
            raise RuntimeError("mongo 连不上，中止归档")
        report = archiver.run(
            start=args.start,
            end=args.end,
            refresh_days=args.refresh_days,
            full_refresh=args.full_refresh,
            dry_run=args.dry_run,
        )
    finally:
        reader.close()
    logger.info(report.summary())
    return report


def run_daemon(cfg: ArchiveConfig, args: argparse.Namespace) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    logger = logging.getLogger(LOGGER_NAME)
    first = True

    def job() -> None:
        # 每次触发都留一条带来源的日志：常驻进程出问题最常见的形式是
        # 「调度没在动」，而这在日志里表现为一段空白，看不出是没触发
        # 还是触发了但没日志。区分「启动补跑」与「定时触发」之后，
        # 一条 `grep 定时触发` 就能回答调度是否活着。
        nonlocal first
        reason = "启动补跑" if first else "定时触发"
        first = False
        logger.info("开始归档（%s）", reason)
        # 调度里吞掉异常：单次失败不该让常驻进程退出，下次触发会重试。
        try:
            run_once(cfg, args)
        except Exception as exc:
            logger.error("定时归档失败: %s", exc, exc_info=True)

    job()  # 启动即先补一次，避免停机期间的空窗

    scheduler = BlockingScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        job,
        trigger="cron",
        hour=cfg.schedule_hour,
        minute=cfg.schedule_minute,
        id="daily_archive",
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "已进入常驻模式，每日 %02d:%02d 归档（%s）",
        cfg.schedule_hour,
        cfg.schedule_minute,
        TIMEZONE,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("收到退出信号，归档进程结束")
        scheduler.shutdown(wait=False)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # 自检放在日志与配置之前：这两者本身就是要检查的对象，
    # 先初始化会把「日志配置读不到」这类问题掩盖成默认行为。
    if args.self_check:
        return self_check()

    setup_logging(verbose=args.verbose)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print("配置错误: %s" % exc, file=sys.stderr)
        return 2

    logging.getLogger(LOGGER_NAME).info(
        "归档目录: %s | mongo: %s:%d/%s",
        cfg.data_dir,
        cfg.mongo.host,
        cfg.mongo.port,
        cfg.mongo.database,
    )

    try:
        if args.daemon:
            run_daemon(cfg, args)
        else:
            report = run_once(cfg, args)
            if report.failed_days:
                return 1
    except Exception as exc:
        logging.getLogger(LOGGER_NAME).error("归档异常终止: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
