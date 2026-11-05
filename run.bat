@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python 이 없습니다. https://www.python.org/downloads/ 에서 설치하세요.
  echo     설치할 때 "Add python.exe to PATH" 에 꼭 체크하세요.
  pause
  exit /b 1
)
where ffmpeg >nul 2>nul || echo [!] FFmpeg 가 없어 고화질 영상 합치기와 MP3 변환이 안 됩니다. README 의 설치 방법을 보세요.
where node >nul 2>nul || echo [!] Node.js 가 없으면 일부 YouTube 화질을 못 가져올 수 있습니다. README 의 설치 방법을 보세요.

if not exist ".venv\Scripts\python.exe" (
  echo 처음 실행이라 필요한 프로그램을 설치합니다. 1~2분 걸려요...
  python -m venv .venv || (echo [!] 가상환경을 만들지 못했습니다. & pause & exit /b 1)
)
echo 최신 버전으로 맞추는 중... (YouTube 가 바뀌면 yt-dlp 업데이트가 필요해요)
".venv\Scripts\python.exe" -m pip install -q -U pip
".venv\Scripts\python.exe" -m pip install -q -U -r requirements.txt || (echo [!] 설치에 실패했습니다. 인터넷 연결을 확인하세요. & pause & exit /b 1)

".venv\Scripts\python.exe" app.py
pause
