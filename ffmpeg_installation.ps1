Invoke-WebRequest "https://github.com/GyanD/codexffmpeg/releases/download/5.1/ffmpeg-5.1-essentials_build.zip" -OutFile "ffmpeg-5.1-essentials_build.zip"
Expand-Archive ffmpeg-5.1-essentials_build.zip
move-item ffmpeg-5.1-essentials_build\ffmpeg-5.1-essentials_build\bin\ffmpeg.exe .\
remove-item -Recurse ffmpeg-5.1-essentials_build.zip, ffmpeg-5.1-essentials_build
Write-Output "`nFFmpeg installation compleated!"