"""`sync_strategy_template_params.py` 字段清单与策略 `apply_params` 的一致性守卫。

背景
----
策略参数有三个来源（类属性 / `cfg/strategy.yaml` / 服务端），
`tests/strategy/test_param_defaults_consistency.py` 守住了前两者的映射关系。
第三个来源在服务端，本端测试够不着，只能靠 `sync_strategy_template_params.py` 手动同步。

该脚本的字段清单是**硬编码的**：漏一个字段，同步后服务端就没有这个字段，
策略会退回到类属性兜底 —— 行为上碰巧正确，但界面上看不到、也调不了，
而且「碰巧正确」会在下次改默认值时立刻变成「两处不一致」。

所以这里用 AST 从 `apply_params` 里**真的**把读取的 key 抽出来做比对，
而不是再抄一遍字段名（抄一遍等于把同一个错误写两份）。

真源是 `apply_params` 中的 `xxx_config.get("<字段名>", <类属性>)` 调用。

除了字段清单，本文件还测 `_merge_params` 的合并语义。脚本默认「只补缺、不覆盖」，
这条语义决定了「界面上调过的参数会不会被冲回代码默认值」，属于脚本的核心行为，
读代码容易看漏，因此用断言固定住。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Any

from sync_strategy_template_params import (
    STRATEGY_FIELDS,
    STRATEGY_META,
    _merge_params,
)

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# 策略快捷名 -> 策略类所在文件。三个映射的键集合必须一致（STRATEGY_META /
# STRATEGY_FIELDS / STRATEGY_SOURCES），由 test_registries_cover_same_strategies 守住：
# 新增策略时漏登记一处，守卫会静默不覆盖该策略。
STRATEGY_SOURCES: dict[str, str] = {
    "swing": "strategy/swing_trading_strategy.py",
    "strong": "strategy/strong_rebound_strategy.py",
    "volume": "strategy/volume_expansion_strategy.py",
}


def _find_get_call(expr: ast.AST, base_name: str) -> str | None:
    """在表达式里找出 `<base_name>.get("<字面量>", ...)` 的第一个字符串参数。

    形如 `params.get("match", {}) or {}` 的写法，赋值语句右侧是 `ast.BoolOp`
    而不是 `ast.Call`，因此这里用 `ast.walk` 递归找，而不是直接判 `isinstance(value, ast.Call)`。

    :param expr: 待检查的表达式节点
    :param base_name: 调用主体变量名，如 params
    :return: 找到的字符串参数，未找到返回 None
    """
    for node in ast.walk(expr):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "get":
            continue
        base = node.func.value
        if not (isinstance(base, ast.Name) and base.id == base_name):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            return str(node.args[0].value)
    return None


def _extract_param_keys(source_file: Path) -> set[tuple[str, str]]:
    """用 AST 抽取策略 `apply_params` 读取的 (节, 字段名) 集合。

    不按类名定位目标：源码文件里**含 `apply_params` 的那个类**就是目标。
    这样类改名不会让守卫失效（按类名找的话，改名后 `target is None` 会抛错，
    但如果同一文件里有多个类就容易找错）。

    识别两种调用：
      - `params.get("<节>", ...)`     建立 局部变量名 -> 节名 的映射
      - `<该变量>.get("<字段>", ...)` 记录 (节, 字段)

    这样拿到的就是「参数真正会被读取的字段」，与注释、文档字符串无关。

    :param source_file: 策略源码路径
    :return: (节, 字段名) 集合
    """
    tree: ast.Module = ast.parse(source_file.read_text(encoding="utf-8"))

    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "apply_params":
                    target = item
                    break
        if target is not None:
            break
    if target is None:
        raise AssertionError(f"{source_file.name} 中未找到 apply_params")

    # 变量名 -> 节名（如 match_config -> match）
    # 注意：带类型注解的赋值在 AST 里是 AnnAssign（`x: T = ...`），不带注解才是 Assign，
    # 两者都要处理，否则 `match_config: dict[str, Any] = params.get("match", {}) or {}`
    # 会被整条漏掉。
    var_to_section: dict[str, str] = {}
    for stmt in ast.walk(target):
        tgt: ast.AST | None = None
        val: ast.AST | None = None
        if isinstance(stmt, ast.AnnAssign):
            tgt, val = stmt.target, stmt.value
        elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            tgt, val = stmt.targets[0], stmt.value
        if not isinstance(tgt, ast.Name) or val is None:
            continue
        section = _find_get_call(val, "params")
        if section is not None:
            var_to_section[tgt.id] = section

    keys: set[tuple[str, str]] = set()
    for node in ast.walk(target):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "get":
            continue
        base = node.func.value
        if not isinstance(base, ast.Name) or base.id not in var_to_section:
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            keys.add((var_to_section[base.id], str(node.args[0].value)))

    if not keys:
        raise AssertionError(
            f"{source_file.name} 的 apply_params 未识别出任何参数读取调用，"
            "AST 匹配规则可能已失效（调用写法变了）"
        )
    return keys


class TestSyncFieldsMatchApplyParams(unittest.TestCase):
    """脚本字段清单必须与 apply_params 读取的 key 完全一致。"""

    def test_registries_cover_same_strategies(self) -> None:
        """三张注册表的键集合必须一致，且与源码映射一一对应。

        新增策略时最容易只改 STRATEGY_META 而漏掉 STRATEGY_FIELDS 或
        STRATEGY_SOURCES；那种情况下守卫会**静默不覆盖**该策略，看上去一切正常。
        """
        self.assertEqual(
            set(STRATEGY_META),
            set(STRATEGY_FIELDS),
            f"STRATEGY_META 与 STRATEGY_FIELDS 键集合不一致："
            f"仅 META {sorted(set(STRATEGY_META) - set(STRATEGY_FIELDS))}，"
            f"仅 FIELDS {sorted(set(STRATEGY_FIELDS) - set(STRATEGY_META))}",
        )
        self.assertEqual(
            set(STRATEGY_META),
            set(STRATEGY_SOURCES),
            f"STRATEGY_META 与 STRATEGY_SOURCES 键集合不一致："
            f"仅 META {sorted(set(STRATEGY_META) - set(STRATEGY_SOURCES))}，"
            f"仅 SOURCES {sorted(set(STRATEGY_SOURCES) - set(STRATEGY_META))}",
        )
        for name, meta in STRATEGY_META.items():
            with self.subTest(strategy=name):
                self.assertTrue(meta.get("label"), f"{name} 缺少 label")
                self.assertTrue(meta.get("yaml_section"), f"{name} 缺少 yaml_section")
                self.assertTrue(meta.get("template_id"), f"{name} 缺少 template_id")

    def test_fields_cover_every_key_read_by_apply_params(self) -> None:
        for name, rel_path in STRATEGY_SOURCES.items():
            with self.subTest(strategy=name):
                real_keys: set[tuple[str, str]] = _extract_param_keys(
                    PROJECT_ROOT / rel_path
                )
                declared: set[tuple[str, str]] = {
                    (group, field) for group, field, _desc in STRATEGY_FIELDS[name]
                }

                missing: set[tuple[str, str]] = real_keys - declared
                extra: set[tuple[str, str]] = declared - real_keys

                self.assertEqual(
                    missing,
                    set(),
                    f"[{name}] apply_params 读取但脚本字段清单缺失"
                    f"（服务端同步不到）: {sorted(missing)}",
                )
                self.assertEqual(
                    extra,
                    set(),
                    f"[{name}] 脚本字段清单里有但 apply_params 不读取"
                    f"（同步了也不生效）: {sorted(extra)}",
                )

    def test_fields_match_yaml_section(self) -> None:
        """字段清单必须与 cfg/strategy.yaml 配置节内的字段集合一致。"""
        import yaml

        with open(PROJECT_ROOT / "cfg" / "strategy.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        for name, meta in STRATEGY_META.items():
            with self.subTest(strategy=name):
                section: str = meta["yaml_section"]
                yaml_node = config.get(section)
                self.assertIsInstance(
                    yaml_node, dict, f"cfg/strategy.yaml 缺少配置节 [{section}]"
                )

                yaml_keys: set[tuple[str, str]] = set()
                for group, fields in yaml_node.items():
                    if isinstance(fields, dict):
                        for field in fields:
                            yaml_keys.add((group, field))

                declared: set[tuple[str, str]] = {
                    (group, field) for group, field, _desc in STRATEGY_FIELDS[name]
                }
                self.assertEqual(
                    yaml_keys,
                    declared,
                    f"cfg/strategy.yaml[{section}] 与脚本字段清单分叉："
                    f"仅 yaml 有 {sorted(yaml_keys - declared)}，"
                    f"仅脚本有 {sorted(declared - yaml_keys)}",
                )

    def test_every_field_has_desc(self) -> None:
        """`desc` 是服务端界面的展示文案，留空会让参数在界面上无法辨认。"""
        for name, fields in STRATEGY_FIELDS.items():
            for group, field, desc in fields:
                with self.subTest(field=f"{name}:{group}.{field}"):
                    self.assertTrue(desc.strip(), f"{name}:{group}.{field} 缺少 desc")


class TestMergeParams(unittest.TestCase):
    """`_merge_params` 的合并语义：补缺不覆盖 / 拉齐覆盖同名键。

    这两条语义直接决定「界面上调过的参数会不会被脚本冲掉」，是本脚本最关键的行为。
    """

    # 本端定义的两个节（模拟 _build_plain 的产物）
    LOCAL: dict[str, dict[str, Any]] = {
        "match": {"sr_window": 10, "trend_adx_threshold": 25.0},
        "sell": {"drawdown_pct": -6.0},
    }

    def test_fill_missing_keeps_server_values(self) -> None:
        """补缺模式（默认）：服务端已有字段保留现值，只补缺失的字段。"""
        current: dict[str, Any] = {
            "match": {"sr_window": 20},  # 界面上把 10 调成了 20
            "sell": {"drawdown_pct": -3.0},  # 界面上把 -6.0 调成了 -3.0
        }
        merged, written = _merge_params(current, self.LOCAL, overwrite=False)

        self.assertEqual(merged["match"]["sr_window"], 20, "界面调参被冲回代码默认值")
        self.assertEqual(merged["sell"]["drawdown_pct"], -3.0, "界面调参被冲回代码默认值")
        self.assertEqual(merged["match"]["trend_adx_threshold"], 25.0, "缺失字段未补入")
        self.assertEqual(written, ["match.trend_adx_threshold"])

    def test_align_overwrites_same_keys(self) -> None:
        """拉齐模式：同名键用本端值覆盖，服务端独有键仍保留。"""
        current: dict[str, Any] = {
            "match": {"sr_window": 20, "sr_tolerance": 0.02},
        }
        merged, written = _merge_params(current, self.LOCAL, overwrite=True)

        self.assertEqual(merged["match"]["sr_window"], 10, "拉齐模式未覆盖同名键")
        self.assertEqual(merged["match"]["sr_tolerance"], 0.02, "服务端独有字段被删除")
        self.assertEqual(
            sorted(written),
            ["match.sr_window", "match.trend_adx_threshold", "sell.drawdown_pct"],
        )

    def test_empty_server_equals_full_write(self) -> None:
        """服务端为空（新建模板、strategy_params 为 {}）时等价于全量写入。"""
        for empty in (None, {}, {"match": None}):
            with self.subTest(current=empty):
                merged, written = _merge_params(empty, self.LOCAL, overwrite=False)
                self.assertEqual(merged, self.LOCAL)
                self.assertEqual(len(written), 3)

    def test_wrapped_template_keeps_existing_desc_and_value(self) -> None:
        """模板的 `{desc, value}` 包装：已有字段连同 desc 一起保留，不被改写。"""
        current: dict[str, Any] = {
            "match": {"sr_window": {"desc": "服务端旧描述", "value": 20}},
        }
        target: dict[str, Any] = {
            "match": {"sr_window": {"desc": "极值滑动窗口", "value": 10}},
        }
        merged, written = _merge_params(current, target, overwrite=False)

        self.assertEqual(
            merged["match"]["sr_window"], {"desc": "服务端旧描述", "value": 20}
        )
        self.assertEqual(written, [])

    def test_does_not_mutate_current(self) -> None:
        """不得就地改写服务端响应对象（调用方还要用它做差异打印）。"""
        current: dict[str, Any] = {"match": {"sr_window": 20}}
        _merge_params(current, self.LOCAL, overwrite=False)
        self.assertEqual(current, {"match": {"sr_window": 20}})


if __name__ == "__main__":
    unittest.main()
