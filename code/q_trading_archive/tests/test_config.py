"""配置加载的边界行为。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archive.config import APP_ROOT, ConfigError, load_config

VALID = """[mongo]
host=db.example.com
port=27017
username=user
password=secret
database=q_share
"""


class ConfigTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def write_cfg(self, text: str, name: str = "stock.cfg") -> Path:
        path = self.tmp_dir / name
        # 用 write_bytes 而非 write_text：Windows 上 write_text 会把 \n 换成 \r\n，
        # 让测试夹具与实际读入的字节不一致。
        path.write_bytes(text.encode("utf-8"))
        return path


class TestLoadConfig(ConfigTestBase):
    def test_minimal_config_ok(self) -> None:
        cfg = load_config(self.write_cfg(VALID))
        self.assertEqual(cfg.mongo.host, "db.example.com")
        self.assertEqual(cfg.mongo.port, 27017)
        self.assertEqual(cfg.mongo.database, "q_share")

    def test_archive_section_optional(self) -> None:
        cfg = load_config(self.write_cfg(VALID))
        self.assertEqual(cfg.refresh_days, 2)
        self.assertEqual(cfg.schedule_hour, 15)
        self.assertEqual(cfg.max_lookback_days, 400)
        self.assertFalse(cfg.write_empty_day)

    def test_default_data_dir_under_app_root(self) -> None:
        cfg = load_config(self.write_cfg(VALID))
        self.assertEqual(cfg.data_dir, (APP_ROOT / "data").resolve())

    def test_absolute_data_dir_kept(self) -> None:
        out = (self.tmp_dir / "out").as_posix()
        cfg = load_config(self.write_cfg(VALID + "\n[archive]\ndata_dir=%s\n" % out))
        self.assertEqual(cfg.data_dir, Path(out))

    def test_archive_overrides_read(self) -> None:
        text = VALID + "\n[archive]\nrefresh_days=5\nwrite_empty_day=true\nschedule_hour=16\n"
        cfg = load_config(self.write_cfg(text))
        self.assertEqual(cfg.refresh_days, 5)
        self.assertTrue(cfg.write_empty_day)
        self.assertEqual(cfg.schedule_hour, 16)

    def test_password_placeholder_rejected(self) -> None:
        text = VALID.replace("password=secret", "password=REPLACE_WITH_MONGO_PASSWORD")
        with self.assertRaises(ConfigError):
            load_config(self.write_cfg(text))

    def test_missing_mongo_section_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            load_config(self.write_cfg("[archive]\ndata_dir=data\n"))

    def test_missing_required_key_rejected(self) -> None:
        text = VALID.replace("username=user\n", "")
        with self.assertRaises(ConfigError):
            load_config(self.write_cfg(text))

    def test_missing_file_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            load_config(self.tmp_dir / "does-not-exist.cfg")

    def test_bad_int_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            load_config(self.write_cfg(VALID + "\n[archive]\nrefresh_days=abc\n"))

    def test_password_special_chars_url_encoded(self) -> None:
        text = VALID.replace("password=secret", "password=p@ss:word/100%")
        cfg = load_config(self.write_cfg(text))
        # 不编码时 @ 会截断连接串的主机部分，报错却是「认证失败」，很难定位。
        self.assertIn("p%40ss%3Aword%2F100%25", cfg.mongo.uri())

    def test_uri_shape(self) -> None:
        cfg = load_config(self.write_cfg(VALID))
        self.assertEqual(cfg.mongo.uri(), "mongodb://user:secret@db.example.com:27017/q_share")


if __name__ == "__main__":
    unittest.main()
