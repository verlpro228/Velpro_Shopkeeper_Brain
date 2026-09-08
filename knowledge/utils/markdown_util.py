import re
from typing import List
from bs4 import BeautifulSoup


class MarkdownTableLinearizer:
    """
    解决：HTML复杂合并单元格、无表头KV表、左上角空置交叉表、原生MD表
    """

    # 抓整个HTML表格：DOTALL让 . 能匹配换行（表格必然跨行），非贪婪避免吞掉后一个table
    HTML_TABLE_PATTERN = re.compile(r"<table.*?>.*?</table>", re.IGNORECASE | re.DOTALL)
    # 抓原生MD表格：三段式=表头行 + |---|分隔行 + 若干数据行，缺一不认（避免误伤正文里的竖线）
    MD_TABLE_PATTERN = re.compile(
        r'((?:^[ \t]*\|.*\|[ \t]*\n)'
        r'(?:^[ \t]*\|[ \t]*[-:]+[-| :]*\|[ \t]*\n)'
        r'(?:^[ \t]*\|.*\|[ \t]*(?:\n|$))*)',
        re.MULTILINE
    )

    # 总入口：把文档里所有表格原地替换成线性文本，非表格内容原样保留
    @classmethod
    def process(cls, content: str) -> str:
        if not content:
            return content

        # 先HTML表：MinerU解析复杂PDF时产出的多是HTML表
        # 子串预判只是省正则开销的快捷开关，真正匹配仍靠 sub
        if "<table" in content.lower():
            content = cls.HTML_TABLE_PATTERN.sub(cls._replace_html_table, content)

        # 再MD表：处理MinerU直接输出的原生管道表
        if "|" in content:
            content = cls.MD_TABLE_PATTERN.sub(cls._replace_md_table, content)

        return content

    # HTML表 -> 二维网格：核心难点是把 rowspan/colspan 合并单元格"摊平"成普通格子
    @classmethod
    def _replace_html_table(cls, match) -> str:
        html_content = match.group(0)
        soup = BeautifulSoup(html_content, "html.parser")  # 用HTML解析器而非字符串切分，容忍残缺标签
        table = soup.find("table")
        if not table: return html_content  # 解析失败：原文返回，不做破坏性替换

        rows = table.find_all("tr")
        if not rows: return html_content

        # 嗅探 HTML 中是否使用了标准的 <th> 表头标签
        has_th = len(table.find_all("th")) > 0  # 有th = 明确告诉我们第一行是表头

        grid = []
        for _ in range(len(rows)):
            grid.append([])

        # 逐行逐格填入网格：grid[行][列] = 单元格文本
        for row_idx, row in enumerate(rows):
            col_idx = 0
            for cell in row.find_all(['td', 'th']):
                # 跳过已被上方 rowspan 占用的列，找到本行第一个空位
                while col_idx < len(grid[row_idx]) and grid[row_idx][col_idx] is not None:
                    col_idx += 1

                rowspan = int(cell.get('rowspan', 1))  # 跨几行，默认1
                colspan = int(cell.get('colspan', 1))  # 跨几列，默认1
                # 使用 separator 保证 <br> 换行能变成空格，不会粘连
                text = cell.get_text(separator=" ", strip=True)

                # 关键：把合并单元格的文本复制填充到它覆盖的每个格子
                # 这样后续按行读取时，每行都是完整数据，不会因合并而"缺列"
                for r in range(row_idx, row_idx + rowspan):
                    while len(grid) <= r: grid.append([])  # rowspan 可能超出已知行数，动态补行
                    while len(grid[r]) < col_idx + colspan: grid[r].append(None)  # 补列占位
                    for c in range(col_idx, col_idx + colspan):
                        grid[r][c] = text
                col_idx += colspan  # 本格子占了colspan列，游标后移

        return cls._grid_to_text(grid, is_md=False, has_th=has_th)

    # 原生MD表 -> 二维网格（结构简单：按 | 切列即可）
    @classmethod
    def _replace_md_table(cls, match) -> str:
        md_text = match.group(0).strip()
        lines = md_text.split('\n')
        grid = []
        for line in lines:
            # 丢弃 |---|:--:| 对齐分隔行：它只表达对齐方式，对语义无价值
            if re.match(r'^[ \t]*\|[ \t\-|:]+\|[ \t]*$', line): continue
            cells = [cell.strip() for cell in line.strip('|').split('|')]  # 去首尾|再按|切列
            grid.append(cells)
        # Markdown 表格天生自带表头结构
        return cls._grid_to_text(grid, is_md=True, has_th=False)

    # 统一出口：网格 -> 一句句自然语言描述（两种表格格式最终都汇到这里）
    @classmethod
    def _grid_to_text(cls, grid: List[List[str]], is_md: bool, has_th: bool) -> str:
        if not grid or not grid[0]: return ""

        cols_count = max(len(r) for r in grid)  # 取最宽行的列数作为总列数
        # 补齐不规则行的列数，防越界
        for r in grid:
            while len(r) < cols_count: r.append("")

        # 判断"第一行是不是表头"：决定走策略1还是策略2
        is_header_row = False
        if is_md or has_th:
            is_header_row = True  # MD表/有th标签：确定有表头
        # 重点防御：如果左上角是空的，这绝对是一个交叉表头！绝不能当废数据丢弃！
        elif grid[0][0] == "":
            is_header_row = True  # 左上角空 = 行头列头交叉表，第一行是列头
        # 一般大于两列的表格，第一行基本都是表头
        elif cols_count > 2:
            is_header_row = True
        # 剩下的情况：两列且无表头特征 -> 视为 键|值 型KV表

        res = []

        if not is_header_row and cols_count == 2:
            # 【策略 1：纯 K-V 表格】每行独立成句，不需要表头做参照
            for r in grid:
                k, v = r[0], r[1]
                if k or v:
                    k_str = k if k else "未知属性"  # 空值兜底，保证句子完整
                    v_str = v if v else "无"
                    res.append(f"- 【{k_str}】：{v_str}。")
        else:
            # 【策略 2：带有表头定义的标准/交叉表格】
            headers = grid[0]  # 第一行是列名，用来给数据"命名"
            for r in grid[1:]:  # 从第二行起才是数据
                # 跳过完全空的数据行
                if not any(r): continue

                subject = r[0] if r[0] else "未知项目"  # 第一列作为"主语"（行头）
                subject_header = headers[0] if headers[0] else ""  # 主语列的列名（交叉表常为空）

                # 把该行其余列拼成 "列名+为+值" 的属性描述
                props = []
                for c in range(1, cols_count):
                    head = headers[c] if headers[c] else f"属性{c}"  # 列名为空时给个占位名
                    val = r[c] if r[c] else ""

                    # 只保留有信息量的值：空、-、/、\、无 这类占位符直接丢弃
                    if val and val not in ('-', '/', '\\', '无'):
                        props.append(f"{head}为{val}")

                if props:
                    prop_str = "，".join(props)
                    if subject_header:
                        # 有主语列名：补一句"对应XX"，让关系更明确
                        res.append(f"- 【{subject}】(对应{subject_header})：{prop_str}。")
                    else:
                        # 针对左上角为空的情况，隐藏对应关系描述
                        res.append(f"- 【{subject}】：{prop_str}。")
                else:
                    # 整行没有效属性：只留主语（如只有分类名的标题行）
                    if subject != "未知项目":
                        res.append(f"- 【{subject}】")

        # 前后加空行：保证线性文本独立成段，不与相邻正文粘连，利于后续按段落切片
        return "\n\n" + "\n".join(res) + "\n\n"