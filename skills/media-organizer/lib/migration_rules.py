"""migration-rules.yaml 解析 + 匹配 engine。

零依赖(只用 stdlib 的 fnmatch + dataclasses + pathlib)。
支持的 YAML 结构(其他结构不解析,直接报错):
  libraries:
    - name: 电影
      classification_id: <UUID,留空可,跑 migrate 时自动查>
      expected_host_paths:
        - /sata14/my/data/movies
  move_rules:
    - pattern: "*S??E??*"
      target: 电视剧

不支持:嵌套 mapping 超过 2 层、引号字符串、不规则缩进。
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Library:
    name: str
    classification_id: str = ""
    expected_host_paths: list[str] = field(default_factory=list)


@dataclass
class MoveRule:
    pattern: str
    target: str


@dataclass
class MigrationConfig:
    libraries: list[Library] = field(default_factory=list)
    move_rules: list[MoveRule] = field(default_factory=list)


# ============ 手写 mini YAML 解析 ============

def _strip_inline_comment(line: str) -> str:
    """剥离行内 # 注释(只在 # 前有空格时分隔)。"""
    out = []
    in_str = False
    for i, c in enumerate(line):
        if c == '"' or c == "'":
            in_str = not in_str
        if c == "#" and not in_str and (i == 0 or line[i - 1] == " "):
            break
        out.append(c)
    return "".join(out).rstrip()


def _parse_scalar(s: str):
    s = s.strip()
    if not s or s.lower() in ("null", "~"):
        return None
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s


def _yaml_load(text: str) -> dict:
    """极简 YAML 解析:只支持本 skill 配置的 mapping + list of mapping 结构。

    输出顶层永远是一个 dict。
    """
    lines = []
    for raw in text.splitlines():
        line = _strip_inline_comment(raw)
        if not line.strip():
            continue
        lines.append(line)

    def parse_block(items: list[str], min_indent: int):
        """从 items[0:] 开始解析,要求每行 indent ≥ min_indent。

        返回 (parsed, rest_items): parsed 是 dict 或 list,rest_items 是未消费的行。
        根据第一行特征自动判断是 dict(以 'key:' 开头)还是 list(以 '- ' 开头)。
        """
        if not items:
            return [], []

        first = items[0].lstrip(" ")
        first_indent = len(items[0]) - len(first)
        if first_indent < min_indent:
            return [], items  # 全部留给调用方

        if first.startswith("- "):
            return _parse_list(items, first_indent)
        else:
            return _parse_mapping(items, first_indent)

    def _parse_list(items: list[str], base_indent: int):
        """解析 list,base_indent 是 '- ' 的缩进。"""
        result = []
        i = 0
        while i < len(items):
            line = items[i]
            stripped = line.lstrip(" ")
            indent = len(line) - len(stripped)
            if indent < base_indent:
                return result, items[i:]
            if indent > base_indent:
                raise ValueError(f"unexpected indent at line: {line!r}")
            if not stripped.startswith("- "):
                raise ValueError(f"expected '- item' at: {line!r}")
            item_content = stripped[2:].strip()
            if not item_content:
                # '- ' 后跟子 block
                sub_items = []
                j = i + 1
                while j < len(items):
                    sub_indent = len(items[j]) - len(items[j].lstrip(" "))
                    if sub_indent <= indent:
                        break
                    sub_items.append(items[j])
                    j += 1
                if sub_items:
                    child, _ = parse_block(sub_items, indent + 2)
                    result.append(child)
                else:
                    result.append(None)
                i = j
            elif ":" in item_content:
                # '- key: val' 或 '- key:'(空值,有子 block)
                k, _, v = item_content.partition(":")
                k = k.strip()
                v = v.strip()
                if v:
                    # 单行 mapping
                    item_dict = {k: _parse_scalar(v)}
                    # 续行:同 indent+2 的更多 'k: v'
                    j = i + 1
                    while j < len(items):
                        sub = items[j].lstrip(" ")
                        sub_indent = len(items[j]) - len(sub)
                        if sub_indent != indent + 2:
                            break
                        if ":" not in sub or sub.startswith("- "):
                            break
                        k2, _, v2 = sub.partition(":")
                        v2 = v2.strip()
                        if v2:
                            item_dict[k2.strip()] = _parse_scalar(v2)
                            j += 1
                        else:
                            # 续行里出现 'key:' 空值,需要把已收集的 + 后续子 block 一起作为 mapping 处理
                            sub_items = [items[j]]  # 这一行 key: (空)
                            jj = j + 1
                            while jj < len(items):
                                sub2_indent = len(items[jj]) - len(items[jj].lstrip(" "))
                                if sub2_indent <= indent + 2:
                                    break
                                sub_items.append(items[jj])
                                jj += 1
                            # 把当前 item_dict + sub_items 作为 mapping 解析
                            child, _ = _parse_mapping(sub_items, indent + 2)
                            item_dict.update(child)
                            j = jj
                            break
                    result.append(item_dict)
                    i = j
                else:
                    # '- key:' 空值,跟后续子 block 一起作为 mapping
                    sub_items = [f"{' ' * (indent + 2)}{k}:"]
                    j = i + 1
                    while j < len(items):
                        sub_indent = len(items[j]) - len(items[j].lstrip(" "))
                        if sub_indent <= indent + 2:
                            break
                        sub_items.append(items[j])
                        j += 1
                    child, _ = _parse_mapping(sub_items, indent + 2)
                    result.append(child)
                    i = j
            else:
                # '- scalar'
                result.append(_parse_scalar(item_content))
                i += 1
        return result, []

    def _parse_mapping(items: list[str], base_indent: int):
        """解析 mapping,base_indent 是 'key:' 的缩进。"""
        result = {}
        i = 0
        while i < len(items):
            line = items[i]
            stripped = line.lstrip(" ")
            indent = len(line) - len(stripped)
            if indent < base_indent:
                return result, items[i:]
            if indent > base_indent:
                raise ValueError(f"unexpected indent at line: {line!r}")
            if ":" not in stripped or stripped.startswith("-"):
                raise ValueError(f"expected 'key:' at: {line!r}")
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if val:
                result[key] = _parse_scalar(val)
                i += 1
            else:
                # 空值,后面是子 block (mapping 或 list)
                sub_items = []
                j = i + 1
                while j < len(items):
                    sub_indent = len(items[j]) - len(items[j].lstrip(" "))
                    if sub_indent <= indent:
                        break
                    sub_items.append(items[j])
                    j += 1
                if sub_items:
                    child, _ = parse_block(sub_items, indent + 2)
                    result[key] = child
                else:
                    result[key] = None
                i = j
        return result, []

    parsed, _ = parse_block(lines, 0)
    if not isinstance(parsed, dict):
        raise ValueError(f"配置文件顶层必须是 mapping,实际: {type(parsed).__name__}")
    return parsed


def load_config(path: str | Path) -> MigrationConfig:
    """加载并校验 YAML 配置。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} 不存在,先 cp migration-rules.yaml.example {p} 再编辑"
        )
    raw = _yaml_load(p.read_text(encoding="utf-8"))

    libs_raw = raw.get("libraries") or []
    libs = [
        Library(
            name=item["name"],
            classification_id=item.get("classification_id", "") or "",
            expected_host_paths=list(item.get("expected_host_paths") or []),
        )
        for item in libs_raw
        if isinstance(item, dict) and item.get("name")
    ]

    rules_raw = raw.get("move_rules") or []
    rules = [
        MoveRule(pattern=item["pattern"], target=item["target"])
        for item in rules_raw
        if isinstance(item, dict) and item.get("pattern") and item.get("target")
    ]

    lib_names = {l.name for l in libs}
    for r in rules:
        if r.target not in lib_names:
            raise ValueError(
                f"move_rules.target={r.target!r} 在 libraries 里找不到对应 name,"
                f"已知 libraries: {sorted(lib_names)}"
            )

    for lib in libs:
        lib.expected_host_paths = [
            str(Path(p).expanduser()).rstrip("/") for p in lib.expected_host_paths
        ]

    return MigrationConfig(libraries=libs, move_rules=rules)


# ============ 匹配 + 反查 ============

def match_rule(filename: str, rules: list[MoveRule]) -> MoveRule | None:
    """fnmatch 顺序匹配,首条命中即返回。规则顺序按配置。"""
    for r in rules:
        if fnmatch.fnmatch(filename, r.pattern):
            return r
    return None


def infer_current_library(
    file_path: str, libraries: list[Library]
) -> Library | None:
    """从 file_path 反查它当前属于哪个 library(精确路径前缀匹配)。"""
    fp_norm = str(Path(file_path)).rstrip("/")
    for lib in libraries:
        for p in lib.expected_host_paths:
            p_norm = str(Path(p)).rstrip("/")
            if fp_norm == p_norm or fp_norm.startswith(p_norm + "/"):
                return lib
    return None