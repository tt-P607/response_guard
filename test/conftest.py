"""response_guard 测试配置。

直接运行插件自带测试时把项目根目录加入 ``sys.path``，使 ``plugins`` 与
``src`` 均可导入。
"""

from __future__ import annotations

import sys
from pathlib import Path

#: 项目根目录。
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
