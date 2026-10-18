"""只读探查：day_hq_tbl / minute_hq_tbl 的真实字段集。

用途：核对归档的列清单是否覆盖了消费侧实际用到的字段。

已知的契约缺口（本脚本用于证实或证伪）：归档的日K 只有 8 列
（code/create_time/open/high/low/close/volume/amount），而
`factor/vwap_factor.py` 需要 `price` 列，`strategy/*` 里多处读 `preclose`。
缺列时 `VwapFactor.calculate()` 返回 0.0 而不是报错，属于静默失效。

运行（容器内，本机无需装 pymongo）：

    docker run --rm -v "$PWD:/src" -w /src q_trading_archive-builder:latest \
        python _probe_hq_schema.py

只读，不写任何库。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from typing import Any

from archive.config import load_config
from archive.mongo_reader import DAY_COLLECTION, MINUTE_COLLECTION, MongoReader

SAMPLE = 300


def describe(reader: MongoReader, collection: str, label: str) -> None:
    coll = reader._db[collection]  # noqa: SLF001 - 探查脚本，直接用底层句柄
    print("=" * 60)
    print("%s（%s）" % (label, collection))

    total = coll.count_documents({})
    print("文档总数: %s" % total)

    field_hits: Counter = Counter()
    for doc in coll.find({}).limit(SAMPLE):
        for key in doc:
            field_hits[key] += 1
    sampled = min(SAMPLE, total) or 1

    print("抽样 %d 条，字段出现情况：" % sampled)
    for key, hits in sorted(field_hits.items(), key=lambda kv: (-kv[1], kv[0])):
        print("  %-18s %5d/%d" % (key, hits, sampled))

    doc = coll.find_one({})
    if doc:
        preview = {}
        for key, value in doc.items():
            text = str(value)
            preview[key] = text if len(text) <= 40 else text[:40] + "..."
        print("样例文档:")
        print("  " + json.dumps(preview, ensure_ascii=False, indent=2).replace("\n", "\n  "))


def main() -> int:
    cfg = load_config()
    reader = MongoReader(cfg.mongo)
    try:
        if not reader.ping():
            print("mongo 连不上", file=sys.stderr)
            return 1
        describe(reader, DAY_COLLECTION, "日K")
        describe(reader, MINUTE_COLLECTION, "分钟K")
    finally:
        reader.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
