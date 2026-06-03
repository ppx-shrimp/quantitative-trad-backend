"""股票量化系统第一版外壳。"""

import os
import sys

# Python 3.8+ 在 Windows 上收紧了 DLL 搜索路径，导致 SQLAlchemy 的 C 扩展
# （cyextension/*.pyd）加载时找不到依赖 DLL 而挂死。需要显式添加 DLL 目录。
if sys.platform == "win32":
    for _dll_dir in (os.path.join(sys.prefix, "DLLs"), sys.prefix):
        if os.path.isdir(_dll_dir):
            os.add_dll_directory(_dll_dir)

__version__ = "0.1.0"
