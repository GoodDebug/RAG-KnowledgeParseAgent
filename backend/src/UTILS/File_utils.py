import os
from typing import List, Optional, Set


class FilePathScanner:
    """
    文件路径扫描工具：仅收集符合后缀的文件完整路径，不读取文件内容
    """
    def __init__(
        self,
        root_dir: str,
        suffix_white_list: Optional[List[str]] = None,
        recursive: bool = True,
        skip_hidden: bool = True,
        dir_blacklist: Optional[Set[str]] = None
    ):
        """
        :param root_dir: 扫描根目录
        :param suffix_white_list: 允许的文件后缀，如 [".md", ".txt"]
        :param recursive: 是否递归遍历子文件夹
        :param skip_hidden: 是否跳过隐藏文件/隐藏文件夹（.开头）
        :param dir_blacklist: 目录黑名单，如 {".git", "venv", "__pycache__"}
        """
        self.root_dir = os.path.abspath(root_dir)
        self.recursive = recursive
        self.skip_hidden = skip_hidden
        # 默认只扫描文本知识库格式
        self.suffix_white_list = suffix_white_list or [".md", ".txt"]
        # 默认屏蔽无用缓存目录
        self.dir_blacklist = dir_blacklist or {".git", "venv", "__pycache__", "cache", "output"}

    def scan(self) -> List[str]:
        """执行扫描，返回所有匹配文件的完整路径列表"""
        path_result: List[str] = []

        if not os.path.isdir(self.root_dir):
            print(f"警告：目录不存在 {self.root_dir}")
            return path_result

        for entry in os.scandir(self.root_dir):
            entry_name = entry.name
            # 跳过隐藏项
            if self.skip_hidden and entry_name.startswith("."):
                continue

            # 处理文件夹
            if entry.is_dir(follow_symlinks=False):
                # 黑名单目录直接跳过
                if entry_name in self.dir_blacklist:
                    continue
                # 递归扫描子目录
                if self.recursive:
                    sub_scanner = FilePathScanner(
                        root_dir=entry.path,
                        suffix_white_list=self.suffix_white_list,
                        recursive=self.recursive,
                        skip_hidden=self.skip_hidden,
                        dir_blacklist=self.dir_blacklist
                    )
                    sub_paths = sub_scanner.scan()
                    path_result.extend(sub_paths)

            # 处理文件：匹配后缀则存入路径
            elif entry.is_file(follow_symlinks=False):
                if any(entry.path.endswith(suf) for suf in self.suffix_white_list):
                    path_result.append(entry.path)

        return path_result


# 简易独立函数（不想用类时使用）
def scan_file_paths(
    root_dir: str,
    suffix_list: Optional[List[str]] = None,
    recursive: bool = True
) -> List[str]:
    """
    极简函数版：只返回匹配格式的文件路径列表
    """
    scanner = FilePathScanner(root_dir, suffix_white_list=suffix_list, recursive=recursive)
    return scanner.scan()


"""

# 测试示例
if __name__ == "__main__":
    # 示例1：扫描md、txt文件，递归子目录
    scanner = FilePathScanner(root_dir="./Study-Test", suffix_white_list=[".md", ".txt"])
    file_path_list = scanner.scan()
    print("匹配文件路径列表：")
    for p in file_path_list:
        print(p)

    # 示例2：仅单层目录，只读取py文件
    paths = scan_file_paths("./Study-Test", suffix_list=[".py"], recursive=False)
    print("\n文件数量：", len(paths))

"""