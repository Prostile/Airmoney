from __future__ import annotations

from pathlib import Path

_SRC_PACKAGE = Path(__file__).resolve().parent.parent / "src" / "airmoney"
__path__ = [str(_SRC_PACKAGE)]

_init_file = _SRC_PACKAGE / "__init__.py"
if _init_file.exists():
    exec(compile(_init_file.read_text(encoding="utf-8"), str(_init_file), "exec"))
