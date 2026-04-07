"""
yomi-KAI ビルドスクリプト
===========================
Portable Python (embeddable) を同封した完全なポータブル配布用パッケージを生成します。
Python、ffmpeg、および VOICEVOX (エンジン・辞書・モデル) は実行時に自動ダウンロードされ、
build_cache/ にキャッシュされます。事前の手動配置作業は不要です。

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
    └── lib/                 ... ユーザーが直接触れないファイル群
         ├── yomi-KAI.py
         ├── ffmpeg.exe          ... FFmpeg バイナリ (自動ダウンロード)
         ├── voicevox_core/      ... モデル・辞書・ONNXランタイム (自動ダウンロード)
         ├── packages/           ... Pythonパッケージ群 (自動インストール)
         └── python/             ... Python Embeddable本体 (自動ダウンロード)
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
PYTHON_VERSION = "3.12.10"
PYTHON_EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
PIP_BOOTSTRAP_URL = "https://bootstrap.pypa.io/get-pip.py"

# ffmpeg のバージョン設定
# リリースノート: https://github.com/GyanD/codexffmpeg/releases
FFMPEG_VERSION = "8.1"
FFMPEG_URL = f"https://github.com/GyanD/codexffmpeg/releases/download/{FFMPEG_VERSION}/ffmpeg-{FFMPEG_VERSION}-essentials_build.zip"

# voicevox_core のバージョン設定
# リリースノート: https://github.com/VOICEVOX/voicevox_core/releases
VOICEVOX_CORE_VERSION = "0.16.4"
VOICEVOX_DOWNLOADER_URL = f"https://github.com/VOICEVOX/voicevox_core/releases/download/{VOICEVOX_CORE_VERSION}/download-windows-x64.exe"



VERSION = sys.argv[1] if len(sys.argv) > 1 else "dev"
DIST_NAME = f"yomi-KAI-v{VERSION}"
DIST_ROOT = Path("./dist") / DIST_NAME
LIB_DIR = DIST_ROOT / "lib"
PYTHON_DIR = LIB_DIR / "python"
PACKAGES_DIR = LIB_DIR / "packages"

# ================================================================
# ステップ管理ユーティリティ
# ================================================================
def step(msg: str):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")

def check(msg: str):
    print(f"  [OK] {msg}")

def warn(msg: str):
    print(f"  [WARN] {msg}")

# ================================================================
# Step 0: 事前チェック
# ================================================================
step("Step 0: 事前チェック")

errors = []
if not Path("./yomi-KAI.py").exists():
    errors.append("yomi-KAI.py が見つかりません。ビルドはプロジェクトルートから実行してください。")

if errors:
    print("\n[ERROR] ビルドを開始できません:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)

ffmpeg_src = None  # 自動ダウンロードするのでここでは未設定
check(f"ffmpeg: v{FFMPEG_VERSION} をダウンロード予定")
check(f"voicevox_core: v{VOICEVOX_CORE_VERSION} をダウンロード予定 (0.vvmのみ同梱)")

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
# Step 3: ffmpeg をダウンロード
# ================================================================
step("Step 3: ffmpeg をダウンロード")

ffmpeg_cache_zip = Path(f"./build_cache/ffmpeg-{FFMPEG_VERSION}-essentials_build.zip")
ffmpeg_exe_cache = Path(f"./build_cache/ffmpeg-{FFMPEG_VERSION}.exe")

if not ffmpeg_exe_cache.exists():
    if not ffmpeg_cache_zip.exists():
        print(f"  ダウンロード中: {FFMPEG_URL}")
        urllib.request.urlretrieve(FFMPEG_URL, ffmpeg_cache_zip)
        check("ダウンロード完了")
    else:
        check(f"キャッシュを使用: {ffmpeg_cache_zip}")

    # zipから ffmpeg.exe だけを取り出す
    with zipfile.ZipFile(ffmpeg_cache_zip, "r") as zf:
        exe_entries = [e for e in zf.namelist() if e.endswith("bin/ffmpeg.exe")]
        if not exe_entries:
            print("[ERROR] zip内に ffmpeg.exe が見つかりませんでした。")
            sys.exit(1)
        with zf.open(exe_entries[0]) as src, open(ffmpeg_exe_cache, "wb") as dst:
            dst.write(src.read())
    check(f"ffmpeg.exe を抽出: {ffmpeg_exe_cache}")
else:
    check(f"キャッシュを使用: {ffmpeg_exe_cache}")

ffmpeg_src = ffmpeg_exe_cache

# ================================================================
# Step 4: pip を有効化してパッケージをインストール
# ================================================================
step("Step 4: 依存パッケージのインストール")

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
# Step 5: voicevox_core のダウンロード
# ================================================================
step("Step 5: voicevox_core をダウンロード")

vvc_cache_dir = Path(f"./build_cache/voicevox_core-{VOICEVOX_CORE_VERSION}")
downloader_exe = Path(f"./build_cache/voicevox-downloader-{VOICEVOX_CORE_VERSION}.exe")

if not downloader_exe.exists():
    print(f"  ダウンロード中: {VOICEVOX_DOWNLOADER_URL}")
    urllib.request.urlretrieve(VOICEVOX_DOWNLOADER_URL, downloader_exe)
    check("ダウンローダー取得完了")
else:
    check(f"キャッシュを使用: {downloader_exe}")

# ダウンローダーがまだ実行されていない場合のみ実行
if not (vvc_cache_dir / "onnxruntime").exists():
    vvc_cache_dir.mkdir(parents=True, exist_ok=True)
    
    # エンジン・辞書のダウンロード (c-apiとmodelsを除外)
    cmd_engine = [str(downloader_exe), "-o", str(vvc_cache_dir), "--exclude", "c-api", "--exclude", "models"]
    print("  voicevox_core エンジンダウンロード中...")
    subprocess.run(cmd_engine, input=b"y\n", check=True)
    
    # 0.vvmのみダウンロード
    cmd_model = [str(downloader_exe), "-o", str(vvc_cache_dir), "--only", "models", "--models-pattern", "0.vvm"]
    print("  voicevox_core 音声モデル(0.vvm)ダウンロード中...")
    subprocess.run(cmd_model, input=b"y\n", check=True)
        
    check(f"voicevox_core ダウンロード完了: {vvc_cache_dir}")
else:
    check(f"キャッシュを使用: {vvc_cache_dir}")

# ================================================================
# Step 6: アセットのコピー
# ================================================================
step("Step 6: アセットをコピー")

# プログラム本体
shutil.copy("./yomi-KAI.py", LIB_DIR / "yomi-KAI.py")
check("yomi-KAI.py → lib/")

# ffmpeg.exe
shutil.copy(str(ffmpeg_src), LIB_DIR / "ffmpeg.exe")
check(f"ffmpeg.exe → lib/ (v{FFMPEG_VERSION})")

# voicevox_core: onnxruntime, dict, および models の 0.vvm のみをコピー
print(f"  voicevox_core をコピー中...")
vvc_dest = LIB_DIR / "voicevox_core"
vvc_dest.mkdir(parents=True)

# onnxruntime
shutil.copytree(str(vvc_cache_dir / "onnxruntime"), str(vvc_dest / "onnxruntime"))
# dict
shutil.copytree(str(vvc_cache_dir / "dict"), str(vvc_dest / "dict"))
# models (0.vvmのみ)
(vvc_dest / "models" / "vvms").mkdir(parents=True)
shutil.copy(str(vvc_cache_dir / "models/vvms/0.vvm"), str(vvc_dest / "models/vvms/0.vvm"))
# モデル関連のライセンス等ファイルがもしあればコピーしておく
for f in (vvc_cache_dir / "models").glob("*.txt"):
    shutil.copy(str(f), str(vvc_dest / "models" / f.name))

check(f"voicevox_core → lib/voicevox_core/ (0.vvmのみを同梱)")

# ユーザー向けファイル
shutil.copy("./config.ini.example", DIST_ROOT / "config.ini.example")
shutil.copy("./README.md", DIST_ROOT / "README.md")
if Path("./icon.ico").exists():
    shutil.copy("./icon.ico", LIB_DIR / "icon.ico")
check("config.ini.example, README.md → ルート")

# ================================================================
# Step 7: yomi-KAI.bat を生成
# ================================================================
step("Step 7: ランチャー (yomi-KAI.bat) を生成")

launcher_content = """\
@echo off
cd /d %~dp0
lib\\python\\python.exe lib\\yomi-KAI.py
pause
"""
(DIST_ROOT / "yomi-KAI.bat").write_text(launcher_content, encoding="utf-8")
check("yomi-KAI.bat を生成しました")

# ================================================================
# Step 8: ZIP圧縮
# ================================================================
step("Step 8: ZIP に圧縮")

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