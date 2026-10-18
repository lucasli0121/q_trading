"""配置加载。

从 cfg/stock.cfg 读取 mongo 连接信息与归档参数。真实配置文件不入库（见
.gitignore），模板见 cfg/stock.cfg.example。

设计取向：任何缺失或非法的 mongo 凭据都直接抛 ConfigError，不用默认值兜底。
静默地用空密码去连库，只会得到一条「连不上」的日志，排查成本远高于启动即失败。
"""

from __future__ import annotations

import configparser
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus


def _resolve_app_root() -> Path:
    """定位「部署根」：cfg / log / data 都相对它解析。

    冻结态（PyInstaller onedir）下 ``__file__`` 落在 ``_internal/`` 里，
    而 cfg 是给运维改的、放在可执行文件旁边。若继续用 ``__file__`` 推导，
    会指向 ``_internal``，读到的是一份没人会去改的副本。
    更麻烦的是 ``ConfigParser.read()`` 读不到文件不抛异常，失败是静默的：
    进程照常启动、日志照常打，只是配置全是默认值。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_ROOT = _resolve_app_root()
DEFAULT_CFG_PATH = APP_ROOT / "cfg" / "stock.cfg"


def candidate_cfg_paths(name: str = "stock.cfg") -> list[Path]:
    """按优先级列出配置文件候选位置。

    部署根优先（运维实际编辑的那份），其次是打包进 ``_internal`` 的兜底副本。
    顺序不能反：反了会出现「改了部署根的配置却不生效」。
    """
    candidates = [APP_ROOT / "cfg" / name]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "cfg" / name)
    return list(dict.fromkeys(candidates))


def _locate_cfg(explicit: str | Path | None) -> Path:
    if explicit:
        return Path(explicit)
    for path in candidate_cfg_paths():
        if path.is_file():
            return path
    return DEFAULT_CFG_PATH


class ConfigError(RuntimeError):
    """配置缺失或非法，进程应立即退出。"""


@dataclass(frozen=True)
class MongoConfig:
    host: str
    port: int
    username: str
    password: str
    database: str

    def uri(self) -> str:
        """构造连接串。

        用户名与密码做 URL 编码：密码里含 @ / : / % 等字符时，
        不做编码会把连接串切错位置，报的却是「认证失败」。
        """
        return "mongodb://%s:%s@%s:%d/%s" % (
            quote_plus(self.username),
            quote_plus(self.password),
            self.host,
            self.port,
            self.database,
        )


@dataclass(frozen=True)
class ArchiveConfig:
    data_dir: Path
    refresh_days: int
    batch_size: int
    schedule_hour: int
    schedule_minute: int
    max_lookback_days: int
    write_empty_day: bool
    mongo: MongoConfig


def _require(section: configparser.SectionProxy, key: str, where: str) -> str:
    value = (section.get(key) or "").strip()
    if not value:
        raise ConfigError("配置项缺失: [%s] %s" % (where, key))
    if value.startswith("REPLACE_WITH_"):
        raise ConfigError(
            "配置项 [%s] %s 仍是模板占位符，请填写真实值" % (where, key)
        )
    return value


def _as_int(section: configparser.SectionProxy, key: str, default: int, where: str) -> int:
    raw = (section.get(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError("[%s] %s 需要整数，实际是 %r" % (where, key, raw)) from exc


def _as_bool(section: configparser.SectionProxy, key: str, default: bool, where: str) -> bool:
    raw = (section.get(key) or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError("[%s] %s 需要布尔值，实际是 %r" % (where, key, raw))


def load_config(path: str | Path | None = None) -> ArchiveConfig:
    """读取并校验配置。"""
    cfg_path = _locate_cfg(path)
    if not cfg_path.is_file():
        searched = "\n  ".join(str(p) for p in candidate_cfg_paths())
        raise ConfigError(
            "配置文件不存在。已查找:\n  %s\n"
            "请先复制模板: cp cfg/stock.cfg.example cfg/stock.cfg" % searched
        )

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(str(cfg_path), encoding="utf-8")

    if not parser.has_section("mongo"):
        raise ConfigError("配置缺少 [mongo] 节: %s" % cfg_path)

    mongo_section = parser["mongo"]
    mongo = MongoConfig(
        host=_require(mongo_section, "host", "mongo"),
        port=int(_require(mongo_section, "port", "mongo")),
        username=_require(mongo_section, "username", "mongo"),
        password=_require(mongo_section, "password", "mongo"),
        database=_require(mongo_section, "database", "mongo"),
    )

    arch = parser["archive"] if parser.has_section("archive") else None

    raw_dir = ""
    if arch is not None:
        raw_dir = (arch.get("data_dir") or "").strip()
    data_dir = Path(raw_dir) if raw_dir else Path("data")
    if not data_dir.is_absolute():
        data_dir = (APP_ROOT / data_dir).resolve()

    return ArchiveConfig(
        data_dir=data_dir,
        refresh_days=_as_int(arch, "refresh_days", 2, "archive") if arch else 2,
        batch_size=_as_int(arch, "batch_size", 20000, "archive") if arch else 20000,
        schedule_hour=_as_int(arch, "schedule_hour", 15, "archive") if arch else 15,
        schedule_minute=_as_int(arch, "schedule_minute", 30, "archive") if arch else 30,
        max_lookback_days=_as_int(arch, "max_lookback_days", 400, "archive") if arch else 400,
        write_empty_day=_as_bool(arch, "write_empty_day", False, "archive") if arch else False,
        mongo=mongo,
    )
