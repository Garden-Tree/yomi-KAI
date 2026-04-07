"""
yomi-KAI ビルドスクリプト
===========================
Portable Python (embeddable) を同封した配布用パッケージを生成します。

使い方:
    python build.py [バージョン]
    例: python build.py 1.2.0

生成物:
    ./dist/yomi-KAI-v{version}/  ... 配布用フォルダ
    ./dist/yomi-KAI-v{version}.zip ... 配布用ZIPファイル

配布フォルダの構成:
    yomi-KAI-v{version}/
    ├── yomi-KAI.bat         ... ダブルクリックで起動するランチャー
    ├── config.ini.example   ... 設定ファイルのサンプル
    ├── README.md
    └── lib/                 ... ユーザーが直接触わらないファイル群
         ├── yomi-KAI.py
         ├── ffmpeg.exe
         ├── voicevox_core/  ... モデル・辞書・ONNXランタイム
         ├── packages/       ... Pythonパッケージ群
         └── python/         ... Python Embeddable本体
"""

import sys
import os
import shutil
import zipfile
import subprocess
import urllib.request
from pathlib import Path

# ================================================================
# 設定
# ================================================================
PYTHON_VERSION = "3.12.9"
PYTHON_EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
PIP_BOOTSTRAP_URL = "https://bootstrap.pypa.io/get-pip.py"

VERSION = sys.argv[1] if len(sys.argv) > 1 else "dev"
DIST_NAME = f"yomi-KAI-v{VERSION}"
DIST_ROOT = Path("./dist") / DIST_NAME
LIB_DIR = DIST_ROOT / "lib"
PYTHON_DIR = LIB_DIR / "python"
PACKAGES_DIR = LIB_DIR / "packages"

# ================================================================
# 同梱する音声モデル (VVM) の設定
# ================================================================
# 新しいキャラクターに対応したときはここにVVMファイル名を追加してください。
# VVMのIDとキャラクター対応表は voicevox_core/models/vvms/ を参照してください。
# （例: 15.vvmがずんだもん、3.vvmが波音リツ、など）
#
# style_idとVVMの対応:
#   ずんだもん    ... 15.vvm (style_id: 3=ノーマル, 1=あまあま, 7=ツンツン 等)
#   春日部つむぎ  ... 8.vvm  (style_id: 8=ノーマル 等)
#
# ※ 新しいキャラを yomi-KAI.py に追加したら、対応するVVM IDをここに追記する。
BUNDLED_VVM_IDS = [
    "15",   # ずんだもん
    "8",    # 春日部つむぎ
]

# ================================================================
# ステップ管理ユーティリティ
# ================================================================
def step(msg: str):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")

def check(msg: str):
    print(f"  ✔ {msg}")

def warn(msg: str):
    print(f"  ⚠ {msg}")

# ================================================================
# Step 0: 事前チェック
# ================================================================
step("Step 0: 事前チェック")

errors = []
if not Path("./yomi-KAI.py").exists():
    errors.append("yomi-KAI.py が見つかりません。ビルドはプロジェクトルートから実行してください。")
if not Path("./ffmpeg.exe").exists() and not Path("./lib/ffmpeg.exe").exists():
    errors.append("ffmpeg.exe が見つかりません。プロジェクトルートまたは lib/ に配置してください。")

voicevox_src = Path("./lib/voicevox_core") if Path("./lib/voicevox_core").exists() else Path("./voicevox_core")
if not voicevox_src.exists():
    errors.append("voicevox_core フォルダが見つかりません。lib/ またはプロジェクトルートに配置してください。")

if errors:
    print("\n[ERROR] ビルドを開始できません:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)

ffmpeg_src = Path("./lib/ffmpeg.exe") if Path("./lib/ffmpeg.exe").exists() else Path("./ffmpeg.exe")
check(f"ffmpeg: {ffmpeg_src}")
check(f"voicevox_core: {voicevox_src}")

# ================================================================
# Step 1: dist クリーンアップ
# ================================================================
step("Step 1: 出力先を初期化")

if DIST_ROOT.exists():
    shutil.rmtree(DIST_ROOT)
DIST_ROOT.mkdir(parents=True)
LIB_DIR.mkdir(parents=True)
PACKAGES_DIR.mkdir(parents=True)
check(f"出力先を作成しました: {DIST_ROOT}")

# ================================================================
# Step 2: Python Embeddable のダウンロード & 展開
# ================================================================
step("Step 2: Python Embeddable をダウンロード")

embed_zip = Path(f"./build_cache/python-{PYTHON_VERSION}-embed-amd64.zip")
embed_zip.parent.mkdir(exist_ok=True)

if not embed_zip.exists():
    print(f"  ダウンロード中: {PYTHON_EMBED_URL}")
    urllib.request.urlretrieve(PYTHON_EMBED_URL, embed_zip)
    check("ダウンロード完了")
else:
    check(f"キャッシュを使用: {embed_zip}")

PYTHON_DIR.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(embed_zip, "r") as zf:
    zf.extractall(PYTHON_DIR)
check(f"Python Embeddable を展開: {PYTHON_DIR}")

# ================================================================
# Step 3: pip を有効化してパッケージをインストール
# ================================================================
step("Step 3: 依存パッケージのインストール")

# python312._pth の site-packages コメントを外してimportを有効化
pth_files = list(PYTHON_DIR.glob("python*._pth"))
if pth_files:
    pth_file = pth_files[0]
    content = pth_file.read_text(encoding="utf-8")
    content = content.replace("#import site", "import site")
    pth_file.write_text(content, encoding="utf-8")
    check(f"import site を有効化: {pth_file.name}")

# get-pip.py のダウンロード
pip_bootstrap = Path("./build_cache/get-pip.py")
if not pip_bootstrap.exists():
    print("  get-pip.py をダウンロード中...")
    urllib.request.urlretrieve(PIP_BOOTSTRAP_URL, pip_bootstrap)
    check("get-pip.py ダウンロード完了")

python_exe = PYTHON_DIR / "python.exe"

# pip インストール
print("  pip をインストール中...")
subprocess.run([str(python_exe), str(pip_bootstrap), "--no-warn-script-location"], check=True)
check("pip インストール完了")

# requirements.txt の依存をパッケージフォルダにインストール
print("  依存パッケージをインストール中...")
subprocess.run([
    str(python_exe), "-m", "pip", "install",
    "-r", "./requirements.txt",
    "--target", str(PACKAGES_DIR),
    "--no-warn-script-location",
], check=True)
check(f"依存パッケージのインストール完了: {PACKAGES_DIR}")

# ================================================================
# Step 4: アセットのコピー
# ================================================================
step("Step 4: アセットをコピー")

# プログラム本体
shutil.copy("./yomi-KAI.py", LIB_DIR / "yomi-KAI.py")
check("yomi-KAI.py → lib/")

# ffmpeg.exe
shutil.copy(str(ffmpeg_src), LIB_DIR / "ffmpeg.exe")
check(f"ffmpeg.exe → lib/ ({ffmpeg_src})")

# voicevox_core: モデル以外のフォルダ (辞書・DLL) はすべてコピー
vvc_dst = LIB_DIR / "voicevox_core"
vvc_dst.mkdir(parents=True, exist_ok=True)
for sub in voicevox_src.iterdir():
    if sub.name == "models":
        continue  # modelsはVVM選別コピーするのでここではスキップ
    dst = vvc_dst / sub.name
    if sub.is_dir():
        shutil.copytree(str(sub), str(dst))
    else:
        shutil.copy(str(sub), str(dst))

# voicevox_core: 音声モデル (VVM) は BUNDLED_VVM_IDS に含まれるものだけコピー
vvms_src = voicevox_src / "models" / "vvms"
vvms_dst = vvc_dst / "models" / "vvms"
vvms_dst.mkdir(parents=True, exist_ok=True)
# モデルフォルダ内の models/ 直下ファイルもコピー (README等)
models_src = voicevox_src / "models"
for f in models_src.iterdir():
    if f.is_file():
        shutil.copy(str(f), str(vvc_dst / "models" / f.name))

bundled_count = 0
for vvm_id in BUNDLED_VVM_IDS:
    vvm_file = vvms_src / f"{vvm_id}.vvm"
    if vvm_file.exists():
        shutil.copy(str(vvm_file), str(vvms_dst / vvm_file.name))
        check(f"VVM: {vvm_id}.vvm → lib/voicevox_core/models/vvms/")
        bundled_count += 1
    else:
        warn(f"VVM '{vvm_id}.vvm' が見つかりませんでした。スキップします。")

check(f"voicevox_core → lib/voicevox_core/ ({bundled_count}/{len(BUNDLED_VVM_IDS)} 音声モデルを同梱)")

# ユーザー向けファイル
shutil.copy("./config.ini.example", DIST_ROOT / "config.ini.example")
shutil.copy("./README.md", DIST_ROOT / "README.md")
if Path("./icon.ico").exists():
    shutil.copy("./icon.ico", LIB_DIR / "icon.ico")
check("config.ini.example, README.md → ルート")

# ================================================================
# Step 5: yomi-KAI.bat を生成
# ================================================================
step("Step 5: ランチャー (yomi-KAI.bat) を生成")

launcher_content = """\
@echo off
cd /d %~dp0
lib\\python\\python.exe lib\\yomi-KAI.py
pause
"""
(DIST_ROOT / "yomi-KAI.bat").write_text(launcher_content, encoding="utf-8")
check("yomi-KAI.bat を生成しました")

# ================================================================
# Step 6: ZIP圧縮
# ================================================================
step("Step 6: ZIP に圧縮")

zip_path = Path("./dist") / f"{DIST_NAME}.zip"
with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for file in DIST_ROOT.rglob("*"):
        arcname = file.relative_to(Path("./dist"))
        zf.write(file, arcname)
        
check(f"ZIPを生成しました: {zip_path}")

# ================================================================
# 完了
# ================================================================
step("ビルド完了！")
print(f"""
  配布フォルダ: {DIST_ROOT.resolve()}
  配布ZIP:     {zip_path.resolve()}

  ユーザーに渡すファイル: {DIST_NAME}.zip
""")