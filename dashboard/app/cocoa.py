"""iOS Cocoa HTML → 干净 HTML 转换。

搬迁自 app.py:234-396,用于 iPhone Shortcut 富文本推送。
原样搬,未改逻辑。
"""


def cocoa_html_to_clean(html: str) -> str:
    """iOS Shortcut 「用多信息文本制作 HTML」产出的 Cocoa HTML Writer 风格 HTML
    → 极空间记事本能渲染的干净 HTML。

    策略:**解析** + **渲染**(不用正则硬转),这样遇到 iOS 任何奇怪变体都能处理。
    解析出结构化 block 列表:每个 block 是 (type, content) 或 (type, extra_data)。
    然后按 type 渲染成极空间能识别的 HTML。

    支持的 block type:
    - h1 / h2 / h3 / p:从 span class (s1/s2/s3/s4) 决定 heading level
    - blank:空段(只含 <br> 或空 span)
    - table:2D cell 列表
    - ul / ol:列表项
    - blockquote:引用
    """
    import re
    from html.parser import HTMLParser

    class _CocoaParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.blocks = []
            self._stack = []
            self._span_class = None

        def _top(self):
            return self._stack[-1] if self._stack else None

        @staticmethod
        def _spans_to_text_block(spans):
            """spans 列表 → ('text', btype, text) 或 None(空白)。"""
            text = "".join(s[0] for s in spans)
            if not text.strip():
                return None
            cls_nums = []
            for s, c in spans:
                if c:
                    m = re.match(r"s(\d)", c)
                    if m: cls_nums.append(int(m.group(1)))
            if cls_nums:
                btype = {1: "h1", 2: "h2", 3: "h3"}.get(min(cls_nums), "p")
            else:
                btype = "p"
            return ("text", btype, text)

        @staticmethod
        def _spans_to_plain(spans):
            return re.sub(r"\s+", " ", "".join(s[0] for s in spans)).strip()

        def handle_starttag(self, tag, attrs):
            if tag == "span":
                self._span_class = dict(attrs).get("class", "")
            elif tag == "p":
                self._stack.append({"kind": "p", "spans": []})
            elif tag == "br":
                top = self._top()
                if top and top["kind"] == "p":
                    top["spans"].append(("\n", None))
            elif tag == "table":
                self._stack.append({"kind": "table", "rows": []})
            elif tag == "tr":
                self._stack.append({"kind": "tr", "cells": []})
            elif tag in ("td", "th"):
                self._stack.append({"kind": "cell", "spans": []})
            elif tag in ("ul", "ol"):
                self._stack.append({"kind": "list", "tag": tag, "items": []})
            elif tag == "li":
                self._stack.append({"kind": "li", "spans": []})
            elif tag == "blockquote":
                self._stack.append({"kind": "blockquote", "spans": []})
            # 忽略:html/head/style/meta/title/body/tbody/thead

        def handle_endtag(self, tag):
            if tag == "span":
                self._span_class = None
            elif tag == "p":
                item = self._stack.pop()
                assert item["kind"] == "p"
                parent = self._top()
                text = "".join(s[0] for s in item["spans"])
                if not text.strip():
                    self.blocks.append(("blank",))
                    return
                if parent is None:
                    blk = self._spans_to_text_block(item["spans"])
                    if blk: self.blocks.append(blk)
                elif parent["kind"] == "cell":
                    parent["spans"].append((text, "p"))
                elif parent["kind"] == "li":
                    parent["spans"].append((text, "p"))
                elif parent["kind"] == "blockquote":
                    parent["spans"].append((text, "p"))
            elif tag == "table":
                tbl = self._stack.pop()
                rows_data = []
                for r in tbl["rows"]:
                    if r["kind"] == "tr":
                        rows_data.append([self._spans_to_plain(c["spans"]) for c in r["cells"]])
                self.blocks.append(("table", rows_data))
            elif tag == "tr":
                tr = self._stack.pop()
                parent = self._top()
                if parent and parent["kind"] == "table":
                    parent["rows"].append(tr)
            elif tag in ("td", "th"):
                cell = self._stack.pop()
                parent = self._top()
                if parent and parent["kind"] == "tr":
                    parent["cells"].append(cell)
            elif tag in ("ul", "ol"):
                lst = self._stack.pop()
                self.blocks.append(("list", lst["tag"], lst["items"]))
            elif tag == "li":
                li = self._stack.pop()
                parent = self._top()
                if parent and parent["kind"] == "list":
                    parent["items"].append(self._spans_to_plain(li["spans"]))
            elif tag == "blockquote":
                bq = self._stack.pop()
                text = self._spans_to_plain(bq["spans"])
                self.blocks.append(("blockquote", text))

        def handle_data(self, data):
            target = self._top()
            if target and target["kind"] in ("p", "cell", "li", "blockquote"):
                target["spans"].append((data, self._span_class))

        def error(self, msg):
            pass

    def _render(blocks):
        out = []
        for b in blocks:
            if b[0] == "blank":
                out.append("<p>&nbsp;</p>")
            elif b[0] == "text":
                _, btype, text = b
                out.append(f"<{btype}>{text}</{btype}>")
            elif b[0] == "table":
                _, rows = b
                rhtml = ""
                for row in rows:
                    cells = "".join(f"<td>{c}</td>" for c in row)
                    rhtml += f"<tr>{cells}</tr>"
                out.append(f'<table border="1">{rhtml}</table>')
            elif b[0] == "list":
                _, tag, items = b
                lhtml = "".join(f"<li>{i}</li>" for i in items)
                out.append(f"<{tag}>{lhtml}</{tag}>")
            elif b[0] == "blockquote":
                _, text = b
                out.append(f"<blockquote>{text}</blockquote>")
        return "\n".join(out)

    parser = _CocoaParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return ""  # 解析失败兜底,服务端会再走"纯文本"路径
    return _render(parser.blocks).strip()
