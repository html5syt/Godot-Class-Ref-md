import xml.etree.ElementTree as ET
import polib
import re
from pathlib import Path
from typing import Dict, Optional
import concurrent.futures
import shutil
import requests
from rapidfuzz import fuzz, utils as fuzzy_utils
from bs4 import BeautifulSoup  # 用于处理HTML标签转义
import argparse
import os


try:
    import torch  # 用于GPU加速
except ImportError:
    print("\033[93mtorch库未安装，无法使用GPU加速。使用CPU进行处理。\033[0m")


class XMLToMarkdownTranslator:
    # 可配置参数
    SKIP_FILES = {}  # 跳过文件列表
    SIMILARITY_THRESHOLD = 0.7  # 相似度匹配阈值
    DOCS_URL = "https://docs.godotengine.org/zh-cn/4.x"  # 文档链接前缀
    LOCALIZED_STRINGS = {
        "class_header": "# {class_name}\n",
        "inherits_from": "*继承自: {inherits}*  \n{emoji}**注意**: 可能在未来的版本中删除或发生变化。\n详细信息：{info}  \n",
        "inherits_from_2": "> *继承自: [{inherits}]({rel_path})*\n\n",
        "version": "*版本: {version}*  \n",
        "brief_description": "\n## 简要描述\n\n{content}\n",
        "description": "\n## 详细描述\n\n{content}\n",
        "brief_description_2": "##\s*简要描述",
        "tutorials": "\n## 教程\n",
        "tutorial_item": "- [{title}]({url})",
        "members": "\n## 成员变量\n",
        "members_table": "| 名称 | 类型 | 描述 |\n|------|------|------|",
        "member_row": "| `{name}` | `{type_}` | {desc}",
        "deprecation_notice": "  **注意**: {notice}",
        "methods": "\n## 方法\n",
        "method_header": "### {name}()",
        "return_type": "*返回类型: `{type_}`*",
        "return_type_enum": "*返回类型: `{type_}` (`{enum}`)*  \n",
        "parameters": "\n**参数:**\n",
        "parameter": "- {index}: `{name}` (`{type_}`)",
        "parameter_default": " [默认: `{default}`]",
        "constants": "\n## 常量\n",
        "constant": "- **`{name}`** = `{value}`",
        "signals": "\n## 信号\n",
        "signal": "- **`{name}`**",
        "warning": "\033[93m{message}\033[0m",  # 黄色警告
        "error": "\033[91m{message}\033[0m",  # 红色错误
    }

    def __init__(
        self, po_file_path: Optional[str] = None, lang_code: Optional[str] = None
    ):
        if po_file_path is None and lang_code is not None:
            po_file_path = self.download_po_file(lang_code)

        if po_file_path:
            self.po = polib.pofile(po_file_path)
            self.translation_dict = self._build_translation_dict()
        else:
            self.translation_dict = {}

        self.class_hierarchy = {}
        self.processed_files = set()
        try:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        except:
            pass

    def download_po_file(self, lang_code: str) -> str:
        """从Weblate下载PO文件"""
        url = f"https://hosted.weblate.org/download/godot-engine/godot-class-reference/{lang_code}/"
        local_path = f"godot-engine-godot-class-reference-{lang_code}.po"

        print(f"正在下载翻译文件: {url}")
        response = requests.get(url, stream=True)
        response.raise_for_status()

        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        return local_path

    def _build_translation_dict(self) -> Dict[str, str]:
        """构建翻译字典，保留原始换行和格式"""
        trans_dict = {}
        for entry in self.po:
            if entry.msgstr:
                trans_dict[entry.msgid] = entry.msgstr
                # 添加去除前后空格的版本
                trans_dict[entry.msgid.strip()] = entry.msgstr.strip()
        return trans_dict

    def _localize(self, key: str, **kwargs) -> str:
        """本地化字符串，处理缺失参数"""
        try:
            return self.LOCALIZED_STRINGS[key].format(**kwargs)
        except KeyError as e:
            # 如果缺少参数，尝试不格式化直接返回
            print(self._localize("warning", message=f"本地化字符串缺少参数 {e}: {key}"))
            return self.LOCALIZED_STRINGS[key]

    def _convert_bbcode_to_markdown(self, text: str) -> str:
        """转换BBCode并保留代码缩进"""
        if not text:
            return text

        # 先处理代码块以保留缩进
        def handle_codeblock(match):
            lang = match.group(1) or "gdscript"
            content = match.group(2)
            # 保留原始缩进
            return f"```{lang}\n{content}\n```"

        text = re.sub(
            r'\[codeblock(?: lang="([^"]+)")?\](.*?)\[/codeblock\]',
            handle_codeblock,
            text,
            flags=re.DOTALL,
        )

        # 2. 处理多语言代码块
        def handle_codeblocks(match):
            gdscript = match.group(1).strip()
            csharp = match.group(2).strip()
            return f"```gdscript\n{gdscript}\n```\n\n```csharp\n{csharp}\n```"

        text = re.sub(
            r"\[codeblocks\]\s*\[gdscript\](.*?)\[/gdscript\].*?\[csharp\](.*?)\[/csharp\].*?\[/codeblocks\]",
            handle_codeblocks,
            text,
            flags=re.DOTALL,
        )

        # 3. 处理内联标签
        replacements = [
            (r"\[b\](.*?)\[/b\]", r"**\1**"),  # 加粗
            (r"\[i\](.*?)\[/i\]", r"*\1*"),  # 斜体
            (r"\[u\](.*?)\[/u\]", r"<u>\1</u>"),  # 下划线
            (r"\[s\](.*?)\[/s\]", r"~~\1~~"),  # 删除线
            (r"\[code\](.*?)\[/code\]", r"`\1`"),  # 内联代码
            (r"\[kbd\](.*?)\[/kbd\]", r"`\1`"),  # 键盘输入
            (r"\[br\]", "\n"),  # 换行符
            (r"\[center\](.*?)\[/center\]", r"<center>\1</center>"),  # 居中
            (r"\[url=(.*?)\](.*?)\[/url\]", r"[\2](\1)"),  # 超链接
            (r"\[url\](.*?)\[/url\]", r"\1"),  # 纯URL
            (r"\[param (.*?)\]", r"`\1`"),  # 参数
        ]

        for pattern, repl in replacements:
            text = re.sub(pattern, repl, text)

        # 4. 处理引用标签（不翻译）
        ref_tags = [
            "class",
            "method",
            "constant",
            "signal",
            "member",
            "enum",
            "annotation",
            "constructor",
            "operator",
            "theme_item",
        ]
        for tag in ref_tags:
            text = re.sub(
                rf"\[{tag} ([^\]]+)\]", lambda m: f"`{m.group(1).split('.')[-1]}`", text
            )

        text = (
            text.replace(":**", "**:")
            .replace(":*", "*:")
            .replace("：**", "**：")
            .replace("*：", "*：")
            .replace("$DOCS_URL", self.DOCS_URL)
        )  # 修复格式

        # 转义HTML标签
        text = BeautifulSoup(text, "html.parser").text
        text = text.replace("<", "[").replace(">", "]")

        return text

    def _translate_text(self, text: str) -> str:
        """翻译文本，使用更快的相似度算法"""
        if not text or not self.translation_dict:
            return self._convert_bbcode_to_markdown(text)

        # 使用RapidFuzz进行快速相似度匹配
        best_match = None
        best_score = 0

        # 预处理文本
        processed_text = fuzzy_utils.default_process(text)

        for src, trans in self.translation_dict.items():
            score = fuzz.ratio(
                processed_text, fuzzy_utils.default_process(src), processor=None
            )
            if score > best_score:
                best_score = score
                best_match = trans
                if score == 100:  # 完全匹配
                    break

        if best_match and best_score >= self.SIMILARITY_THRESHOLD * 100:
            if best_score < 100:
                print(f"\033[93m相似度匹配 ({best_score}%): {text[:50]}...\033[0m")
            return self._convert_bbcode_to_markdown(best_match)

        return self._convert_bbcode_to_markdown(text)


    def _get_deprecation_notice(self, elem: ET.Element) -> Optional[str]:
        """获取弃用/实验性说明"""
        if deprecated := elem.get("deprecated"):
            return self._convert_bbcode_to_markdown(deprecated)
        if experimental := elem.get("experimental"):
            return self._convert_bbcode_to_markdown(experimental)
        return None

    def _process_xml_file(self, xml_file: Path) -> Optional[Dict]:
        """处理单个XML文件，返回处理结果和类信息"""
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
        except ET.ParseError as e:
            print(
                self._localize(
                    "error", message=f"XML解析错误 {xml_file.name}: {str(e)}"
                )
            )
            return None

        class_name = root.get("name", "Class")
        inherits = root.get("inherits")

        # 存储类继承关系
        self.class_hierarchy[class_name] = inherits

        # 构建Markdown内容
        md_content = self.xml_to_markdown(root)

        return {
            "class_name": class_name,
            "inherits": inherits,
            "content": md_content,
            "source_file": xml_file,
        }

    def xml_to_markdown(self, root: ET.Element) -> str:
        """将XML元素转换为Markdown文档"""
        md_lines = []

        # 1. 类名标题
        class_name = root.get("name", "Class")
        md_lines.append(self._localize("class_header", class_name=class_name))

        # 2. 继承信息
        inherits = root.get("inherits")
        if inherits:
            emoji = (
                "⚠️"
                if root.get("deprecated")
                else "🔬"
                if root.get("experimental")
                else ""
            )
            info = (
                self._translate_text(root.get("deprecated"))
                if emoji == "⚠️"
                else self._translate_text(root.get("experimental"))
                if emoji == "🔬"
                else "None"
            )
            md_lines.append(
                self._localize(
                    "inherits_from", inherits=inherits, emoji=emoji, info=info
                )
            )

        # 3. 版本信息
        if version := root.get("version"):
            md_lines.append(self._localize("version", version=version))

        # 4. 简要描述
        if (brief := root.find("brief_description")) is not None and brief.text:
            translated = self._translate_text(brief.text)
            md_lines.append(self._localize("brief_description", content=translated))

        # 5. 详细描述
        if (desc := root.find("description")) is not None and desc.text:
            translated = self._translate_text(desc.text)
            md_lines.append(self._localize("description", content=translated))

        # 6. 教程链接
        if (tutorials := root.find("tutorials")) is not None and len(tutorials) > 0:
            md_lines.append(self._localize("tutorials"))
            for link in tutorials.findall("link"):
                title = self._translate_text(link.get("title", "教程链接"))
                url = link.text.replace("$DOCS_URL", self.DOCS_URL)
                md_lines.append(self._localize("tutorial_item", title=title, url=url))
            md_lines.append("")

        # 7. 成员变量表格
        if (members := root.find("members")) is not None and len(members) > 0:
            md_lines.append(self._localize("members"))
            md_lines.append(self._localize("members_table"))
            for member in members.findall("member"):
                name = member.get("name", "")
                type_ = member.get("type", "")
                desc = self._translate_text(member.text if member.text else "")

                row = self._localize(
                    "member_row",
                    name=name.replace("\n", "").replace("\r", ""),
                    type_=type_.replace("\n", "").replace("\r", ""),
                    desc=desc.replace("\n", "").replace("\r", ""),
                )
                if notice := self._get_deprecation_notice(member):
                    row += self._localize("deprecation_notice", notice=notice)
                md_lines.append(row + " |")
            md_lines.append("")

        # 8. 方法文档
        if (methods := root.find("methods")) is not None and len(methods) > 0:
            md_lines.append(self._localize("methods"))
            for method in methods.findall("method"):
                name = method.get("name", "")
                md_lines.append(self._localize("method_header", name=name))

                if notice := self._get_deprecation_notice(method):
                    md_lines[-1] += " ⚠️"
                    md_lines.append(self._localize("deprecation_notice", notice=notice))
                else:
                    md_lines.append("")

                # 返回类型
                if (return_type := method.find("return")) is not None:
                    type_ = return_type.get("type", "void")
                    if enum := return_type.get("enum"):
                        md_lines.append(
                            self._localize("return_type_enum", type_=type_, enum=enum)
                        )
                    else:
                        md_lines.append(
                            self._localize("return_type", type_=type_) + "  \n"
                        )

                # 参数列表
                if (args := method.findall("argument")) and len(args) > 0:
                    md_lines.append(self._localize("parameters"))
                    for arg in args:
                        param = self._localize(
                            "parameter",
                            index=arg.get("index", ""),
                            name=arg.get("name", ""),
                            type_=arg.get("type", ""),
                        )
                        if (default := arg.get("default")) is not None:
                            param += self._localize(
                                "parameter_default", default=default
                            )
                        md_lines.append(param)

                # 方法描述
                if (
                    method_desc := method.find("description")
                ) is not None and method_desc.text:
                    translated = self._translate_text(method_desc.text)
                    md_lines.append("\n" + translated + "\n")

        # 9. 常量和信号
        for section, title_key in [("constants", "constants"), ("signals", "signals")]:
            if (elem := root.find(section)) is not None and len(elem) > 0:
                md_lines.append(self._localize(title_key))
                for item in elem.findall("*"):
                    name = item.get("name", "")
                    if section == "constants":
                        value = item.get("value", "")
                        line = f"- **`{name}`** = `{value}`"
                    else:
                        line = f"- **`{name}`**"

                    if notice := self._get_deprecation_notice(item):
                        line += " ⚠️"
                        line += self._localize("deprecation_notice", notice=notice)

                    line += (
                        f"  \n{self._translate_text(item.text if item.text else '')}\n"
                    )
                    md_lines.append(line)

        return "\n".join(md_lines)

    def _organize_by_hierarchy(self, output_dir: Path):
        """根据继承关系组织文件结构"""
        print("\n正在根据继承关系组织文件结构...")
        temp_dir = output_dir / "_temp"
        temp_dir.mkdir(exist_ok=True)

        # 先移动所有文件到临时目录
        for md_file in output_dir.glob("*.md"):
            if md_file.name not in self.SKIP_FILES:
                shutil.move(str(md_file), str(temp_dir / md_file.name))

        # 创建目录结构并移动文件
        for class_name, inherits in self.class_hierarchy.items():
            md_file = temp_dir / f"{class_name}.md"
            if not md_file.exists():
                continue

            target_dir = output_dir
            if inherits:
                # 查找继承链上的所有父类
                parent_class = inherits
                inheritance_chain = []
                while parent_class and parent_class in self.class_hierarchy:
                    inheritance_chain.append(parent_class)
                    parent_class = self.class_hierarchy[parent_class]

                # 创建完整的继承路径
                if inheritance_chain:
                    target_dir = output_dir / "/".join(reversed(inheritance_chain))
                    target_dir.mkdir(parents=True, exist_ok=True)

                # 添加父类链接到文件第二行
                if inherits:
                    with open(md_file, "r+", encoding="utf-8") as f:
                        content = f.readlines()
                        # 确保有至少一行（标题）
                        if len(content) > 0:
                            # 计算相对路径
                            rel_path = f"{inherits}.md"
                            if inherits in self.class_hierarchy:
                                rel_path = "../" + f"{inherits}.md"

                            # 构建父类链接行
                            parent_link = self._localize(
                                "inherits_from_2", rel_path=rel_path, inherits=inherits
                            )

                            # 覆写第二+1行（如果内容少于2行则追加）
                            if len(content) >= 2:
                                content[2] = parent_link
                            else:
                                content.append(parent_link)

                            # 回写文件
                            f.seek(0)
                            f.writelines(content)
                            f.truncate()

            shutil.move(str(md_file), str(target_dir / md_file.name))

        # 清理临时目录
        shutil.rmtree(temp_dir)
        print("文件结构组织完成")

    def process_directory(self, xml_dir: str, output_dir: str):
        """批量处理目录"""
        xml_dir = Path(xml_dir)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 使用多线程处理文件
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = []
            for xml_file in sorted(xml_dir.glob("*.xml")):
                if xml_file.name in self.SKIP_FILES:
                    print(
                        self._localize("warning", message=f"跳过文件: {xml_file.name}")
                    )
                    continue
                futures.append(executor.submit(self._process_xml_file, xml_file))

            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if not result:
                    continue

                output_path = output_dir / f"{result['class_name']}.md"
                output_path.write_text(result["content"], encoding="utf-8")
                print(f"\n成功生成: {output_path}")
                self.processed_files.add(result["class_name"])

        # 二次处理：根据继承关系组织文件
        self._organize_by_hierarchy(output_dir)

def generate_context_with_descriptions(self: XMLToMarkdownTranslator,directory):
    output_lines = []
    
    def process_directory(path, prefix=''):
        entries = sorted(os.listdir(path))
        for i, entry in enumerate(entries):
            full_path = os.path.join(path, entry)
            is_last = i == len(entries) - 1
            
            if os.path.isdir(full_path):
                line = prefix + ('└── ' if is_last else '├─ ') + entry
                output_lines.append(line)
                new_prefix = prefix + (' ' if is_last else '│ ')
                process_directory(full_path, new_prefix)
            else:
                line = prefix + ('└── ' if is_last else '├─ ') + entry
                output_lines.append(line)
                
                # Check for corresponding .md file
                if entry.lower().endswith('.md'):
                    md_path = full_path
                else:
                    md_path = os.path.splitext(full_path)[0] + '.md'
                
                description = ''
                if os.path.exists(md_path):
                    with open(md_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        # Find the "## 简要描述" section
                        match = re.search(XMLToMarkdownTranslator._localize(self=self,key='brief_description_2')+r'\s*\n+(.*?)(?=\n|\Z)', content, re.DOTALL)
                        if match:
                            description = match.group(1).strip()
                            # Clean up the description
                            description = re.sub(r'\s+', ' ', description)  # Replace multiple spaces/newlines
                            description = description[:18]+"..." if len(description) > 18 else description  # Limit to 18 characters
                
                if description:
                    output_lines[-1]=output_lines[-1]+': '+description
    
    output_lines.append('.')
    process_directory(directory)
    
    # Write to context.txt
    with open(os.path.join(directory, 'context.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(output_lines))
    
    output_lines.append('.')
    process_directory(directory)
    
    # Write to context.txt
    with open(os.path.join(directory, 'context.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(output_lines))

def main():
    parser = argparse.ArgumentParser(
        description="Godot Engine Class Reference markdown Generator"
    )
    parser.add_argument(
        "-L", "--lang", help="Language code (e.g. zh_Hans) ", default=None
    )
    parser.add_argument(
        "-I", "--input", help="Input directory", default="godot/doc/classes"
    )
    parser.add_argument(
        "-O", "--output", help="Output directory", default="translated_markdown"
    )
    parser.add_argument(
        "-E", "--exclude", nargs="+", help="Exclude file list", default=[]
    )

    args = parser.parse_args()

    translator = XMLToMarkdownTranslator(lang_code=args.lang)
    translator.SKIP_FILES = set(args.exclude)

    print(
        f"Translating to {args.lang if args.lang else 'Processing without translation'}"
    )
    translator.process_directory(args.input, args.output)
    
    # 生成目录结构
    generate_context_with_descriptions(translator, args.output)


if __name__ == "__main__":
    main()
