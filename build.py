import shutil
from cx_Freeze import setup, Executable

exe = Executable(script = "./yomi-KAI.py", icon='icon.ico')

setup(name = "yomi-KAI", executables = [exe])

shutil.copytree("./build/exe.win-amd64-3.9/", "./yomi-KAI-v")
shutil.copy("./yomi-KAI.py", "./yomi-KAI-v")
shutil.copy("./config.ini.example", "./yomi-KAI-v")
shutil.copy("./windows_setup.bat", "./yomi-KAI-v")
shutil.copy("./ffmpeg_installation.ps1", "./yomi-KAI-v")
