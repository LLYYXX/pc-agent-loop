"""Engineering validator for the lightweight deadline.

Core口径（仅以下文件计入）：``__init__.py`` + ``overlay.py`` + ``read.py`` +
``select.py`` + ``navigate.py``。``_config.py`` / ``search.py`` 属 substrate，
不算 core；语义主路径（write contract、defense filter、proposal/evidence
audit、BuildSession 统计）必须留在 core，**不得**为压行数把其搬到这些模块
或藏到工具脚本里。

主口径：**core physical lines < 1000**。辅助参考：非空非注释行数。
运行：``python memory/local_semantic_overlay/verify_core_lines.py``
"""

from __future__ import annotations

import pathlib
import sys

CORE = ("__init__.py", "overlay.py", "read.py", "select.py", "navigate.py")
LIMIT = 1000


def main() -> int:
    pkg = pathlib.Path(__file__).resolve().parent
    total_phys = total_nc = 0
    print(f"{'file':<18}{'phys':>8}{'nc':>8}")
    print("-" * 34)
    for f in CORE:
        lines = (pkg / f).read_text(encoding="utf-8").splitlines()
        phys = len(lines)
        nc = sum(1 for l in lines if l.strip() and not l.strip().startswith("#"))
        total_phys += phys; total_nc += nc
        print(f"{f:<18}{phys:>8}{nc:>8}")
    print("-" * 34)
    print(f"{'CORE':<18}{total_phys:>8}{total_nc:>8}   limit={LIMIT} (physical)")
    if total_phys >= LIMIT:
        print(f"FAIL: core physical {total_phys} >= {LIMIT}", file=sys.stderr)
        return 1
    print(f"OK: core physical {total_phys} < {LIMIT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

