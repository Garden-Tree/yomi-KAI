"""yomi-KAI 起動スクリプト。

本体は ``yomi_kai`` パッケージにある。このファイルは配布版（lib/ 配下に置かれる構成）向けの
パス解決だけを行い、``yomi_kai.main.main()`` を呼び出す。
"""

import os
import sys
from pathlib import Path

# --- 配布版パス解決 ---
# yomi-KAI.py が lib/ の中にある場合（配布版）、
# カレントディレクトリをルート（起動.bat と同じ階層）に割り当てる
_script_dir = Path(__file__).resolve().parent
if _script_dir.name == "lib":
    os.chdir(_script_dir.parent)

# lib/packagesが存在する場合（配布版）、sys.pathに追加して優先的に読み込む
_pkg_path = _script_dir / "packages"
if _pkg_path.exists():
    sys.path.insert(0, str(_pkg_path))

# yomi_kai パッケージは本スクリプトと同じ階層にある（配布版では lib/yomi_kai/）
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

if __name__ == "__main__":
    from yomi_kai.main import main

    main()
