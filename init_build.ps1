Invoke-WebRequest "https://www.python.org/ftp/python/3.9.7/python-3.9.7-embed-amd64.zip" -OutFile "python-3.9.7-embed-amd64.zip"
Expand-Archive .\python-3.9.7-embed-amd64.zip
cd .\python-3.9.7-embed-amd64
$data = Get-Content .\python39._pth | ForEach-Object { $_ -replace "#import","import" }
$data | Out-File .\python39._pth
Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile "get-pip.py"
.\python.exe .\get-pip.py
.\python.exe -m pip install discord.py[voice]
Invoke-WebRequest "https://download.lfd.uci.edu/pythonlibs/y2rycu7g/PyAudio-0.2.11-cp39-cp39-win_amd64.whl" -OutFile "PyAudio-0.2.11-cp39-cp39-win_amd64.whl"
.\python.exe -m pip install PyAudio-0.2.11-cp39-cp39-win_amd64.whl
.\python.exe -m pip install python-voicetext
Write-Output "Environment installation compleated!"
pause