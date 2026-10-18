"""归档进度。

记录最后一次成功归档的交易日，供下一次增量推进。文件放在 data 目录同级，
与产物一起被 .gitignore 忽略。

为什么不靠「文件是否已存在」判断进度：盘中归档会写出一个不完整的当日文件，
它「存在」但不能算已完成。有了进度位，再配合每次重刷最近若干天，就能覆盖掉
这类半截数据。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

STATE_VERSION = 1


class ArchiveState:
    """轻量进度文件（JSON）。"""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._logger = logging.getLogger("archive.state")
        self._data: dict[str, Any] = {}
        # 构造即加载。曾经把加载留成显式调用，结果调用方漏了一次，
        # last_date 恒为 None ⇒ 每次运行都退化成全量重跑，而且不报任何错。
        # 这类「忘了调」的失效没有征兆，不如在这里直接读掉。
        self.load()

    def load(self) -> "ArchiveState":
        if not self._path.is_file():
            self._data = {}
            return self
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            self._data = payload if isinstance(payload, dict) else {}
        except (OSError, ValueError) as exc:
            # 进度文件损坏只影响增量起点，不该让进程起不来；退回全量重刷。
            self._logger.warning("进度文件读取失败，将按无进度处理: %s", exc)
            self._data = {}
        return self

    def save(self, last_date: str, extra: dict[str, Any] | None = None) -> None:
        self._data = {
            "version": STATE_VERSION,
            "last_date": last_date,
        }
        if extra:
            self._data.update(extra)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self._data, fh, ensure_ascii=False, indent=1)
        tmp.replace(self._path)

    @property
    def last_date(self) -> str | None:
        value = self._data.get("last_date")
        return str(value) if value else None

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)
