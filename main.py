"""兼容入口：等价于 ``python -m douban_reader``。

保留这个文件只是为了让既有习惯（IDE 运行配置、双击运行）继续可用；
真正的实现全在 ``douban_reader/`` 包里。
"""

from __future__ import annotations

import sys

from douban_reader.cli import main

if __name__ == "__main__":
    sys.exit(main())
