
#!/bin/bash
# これ使えない
if [ ! -d python-3.9.7-embed-amd64 ]; then
    wget https://www.python.org/ftp/python/3.9.7/python-3.9.7-embed-amd64.zip
    unzip python-3.9.7-embed-amd64.zip -d python-3.9.7-embed-amd64
    cd ./python-3.9.7-embed-amd64
    sed -i -e "s/!import/import/g" python39._pth
    wget https://bootstrap.pypa.io/get-pip.py -o get-pip.py
    ./python.exe .\get-pip.py
    ./python.exe -m pip install discord.py[voice]
    wget https://download.lfd.uci.edu/pythonlibs/y2rycu7g/PyAudio-0.2.11-cp39-cp39-win_amd64.whl
    ./python.exe -m pip install PyAudio-0.2.11-cp39-cp39-win_amd64.whl
    ./python.exe -m pip install python-voicetext
    wget https://github.com/GyanD/codexffmpeg/releases/download/4.4/ffmpeg-4.4-essentials_build.zip
    unzip ffmpeg-4.4-essentials_build.zip -d ffmpeg-4.4-essentials_build
    mv ffmpeg-4.4-essentials_build/ffmpeg-4.4-essentials_build/bin/ffmpeg.exe ./
    rm -r get-pip.py PyAudio-0.2.11-cp39-cp39-win_amd64.whl ffmpeg-4.4-essentials_build.zip ffmpeg-4.4-essentials_build
    cd ../
    rm python-3.9.7-embed-amd64.zip
    echo -e "\nEnvironment installation compleated!"
fi

./python-3.9.7-embed-amd64/python.exe yomi-KAI.py
read