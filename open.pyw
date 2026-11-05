"""통합 다운로더를 앱 창으로 연다 (바탕화면 아이콘이 이 파일을 실행).

- 서버가 꺼져 있으면 콘솔 창 없이 켠다 (기록은 server.log)
- 크롬(없으면 엣지) '앱 창'으로 http://127.0.0.1:5000 을 연다
- 유튜브 대응을 위해 yt-dlp 를 3일에 한 번 뒤에서 조용히 업데이트한다
"""
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
URL = "http://127.0.0.1:5000"
NO_WINDOW = 0x08000000
DETACHED = 0x00000008
BROWSERS = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
]


def up():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", 5000)) == 0


def python(windowless=True):
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if windowless and w.exists() else exe.with_name("python.exe"))


def update_tools():
    """yt-dlp 는 유튜브가 바뀌면 자주 고장 나서, 3일마다 최신으로 올린다 (뒤에서, 창 없이)."""
    mark = HERE / ".last-update"
    if mark.exists() and time.time() - mark.stat().st_mtime < 3 * 86400:
        return
    mark.touch()
    log = open(HERE / "update.log", "a", encoding="utf-8")
    subprocess.Popen([python(False), "-m", "pip", "install", "-q", "-U", "-r", str(HERE / "requirements.txt")],
                     cwd=str(HERE), stdout=log, stderr=log, creationflags=NO_WINDOW | DETACHED)


def start_server():
    log = open(HERE / "server.log", "a", encoding="utf-8")
    env = {**os.environ, "NO_BROWSER": "1", "PYTHONIOENCODING": "utf-8"}
    subprocess.Popen([python(), str(HERE / "app.py")], cwd=str(HERE), env=env, stdout=log, stderr=log,
                     creationflags=NO_WINDOW | DETACHED)
    for _ in range(60):
        time.sleep(0.5)
        if up():
            return True
    return False


def open_app():
    for exe in BROWSERS:
        if exe.is_file():
            subprocess.Popen([str(exe), f"--app={URL}", "--window-size=980,900"])
            return
    webbrowser.open(URL)


if __name__ == "__main__":
    if not up():
        update_tools()
        start_server()
    open_app()
