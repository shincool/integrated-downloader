"""통합 다운로더 (로컬 웹 버전) — YouTube · Instagram · TikTok. Flask + yt-dlp + gallery-dl."""
import json
import os
import re
import shutil
import subprocess
import urllib.request
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
from urllib.parse import urlparse

import yt_dlp

import channels
from flask import Flask, abort, jsonify, render_template, request, send_file
from yt_dlp.utils import sanitize_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
COOKIE_FILE = os.path.join(BASE_DIR, "cookies.txt")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = Flask(__name__)
jobs = {}  # job_id -> 상태 dict
jobs_lock = threading.Lock()

# 인스타 사진 게시물: 로그인 없이 브라우저(Playwright)로 모은 사진 주소 (조회 → 다운로드 사이에 기억)
# Playwright 가 이 파이썬에 깔려 있으면 그걸 쓰고, 없으면 옆 폴더 web-capture 의 것을 빌려 쓴다 (선택 기능)
def find_browser_python():
    try:
        import importlib.util
        if importlib.util.find_spec("playwright"):
            return sys.executable
    except Exception:
        pass
    sibling = os.path.join(os.path.dirname(BASE_DIR), "web-capture", ".venv", "Scripts", "python.exe")
    return sibling if os.path.exists(sibling) else None


BROWSER_PY = find_browser_python()
browser_photos = {}


def browser_photo_urls(url):
    if not BROWSER_PY:
        raise RuntimeError("Playwright 가 설치되지 않아 로그인 없는 사진 받기를 쓸 수 없습니다 (README 의 '선택 기능' 참고).")
    r = subprocess.run([BROWSER_PY, "-X", "utf8", os.path.join(BASE_DIR, "ig_browser.py"), url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    line = (r.stdout.strip().splitlines() or [""])[-1]
    data = json.loads(line) if line.startswith("{") else {}
    if not data.get("images"):
        raise RuntimeError("브라우저로도 사진을 찾지 못했습니다.")
    return data

# 공통 옵션: Node.js로 YouTube JS를 풀어 모든 화질 형식을 받아옴
BASE_OPTS = {"quiet": True, "no_warnings": True, "js_runtimes": {"node": {}, "deno": {}}}
LOGIN_CHOICES = {"none", "file", "firefox", "edge", "chrome"}


def update_job(job_id, **fields):
    with jobs_lock:
        jobs[job_id].update(fields)


def valid_url(url):
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def detect_platform(url):
    host = urlparse(url).netloc.lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    return "other"


def ytdlp_opts(url, login):
    """플랫폼·로그인 설정에 맞는 yt-dlp 기본 옵션."""
    opts = dict(BASE_OPTS)
    # YouTube 주소의 &list= 는 무시하되, Instagram 여러 장 게시물은 전체 항목을 받아야 함
    opts["noplaylist"] = detect_platform(url) != "instagram"
    if login == "file":
        opts["cookiefile"] = COOKIE_FILE
    elif login in ("firefox", "edge", "chrome"):
        opts["cookiesfrombrowser"] = (login,)
    return opts


def gallery_dl_cmd(login):
    cmd = [sys.executable, "-m", "gallery_dl"]
    if login == "file":
        cmd += ["--cookies", COOKIE_FILE]
    elif login in ("firefox", "edge", "chrome"):
        cmd += ["--cookies-from-browser", login]
    return cmd


def friendly_error(msg, platform):
    """도구의 영문 오류를 사용자가 할 일이 보이는 한국어 안내로 바꿈."""
    low = msg.lower()
    if "ip address is blocked" in low:
        return "이 게시물은 지역 제한으로 한국에서 받을 수 없습니다."
    if "cookies.txt" in low or ("no such file" in low and "cookie" in low):
        return "cookies.txt 파일이 없습니다. yt-web 폴더에 cookies.txt 를 넣거나 로그인 설정을 바꾸세요."
    if "decrypt" in low or "could not copy" in low or "cookie database" in low:
        return ("브라우저 로그인 정보를 읽지 못했습니다. 해당 브라우저를 완전히 종료한 뒤 다시 시도하거나, "
                "Firefox 또는 cookies.txt 방식을 쓰세요. (최신 크롬·엣지는 읽기가 막혀 있을 수 있습니다)")
    if platform == "instagram" and any(k in low for k in ("login", "log in", "rate-limit", "not available", "empty media")):
        return "Instagram이 로그인을 요구합니다. 위쪽 '로그인' 설정에서 브라우저나 cookies.txt 를 선택한 뒤 다시 시도하세요."
    if "private" in low:
        return "비공개 게시물입니다. 팔로우 중인 계정으로 로그인 설정을 켜고 다시 시도하세요."
    return msg.replace("ERROR: ", "")


def gallery_dl_urls(url, login):
    """gallery-dl로 게시물의 미디어 주소 목록만 조회 (다운로드하지 않음)."""
    r = subprocess.run(gallery_dl_cmd(login) + ["-g", url], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    urls = [u for u in r.stdout.splitlines() if u.startswith("http")]
    if not urls:
        raise RuntimeError(r.stderr.strip() or "가져올 사진이 없습니다.")
    return urls


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/api/info")
def info():
    body = request.get_json(silent=True) or {}
    url = body.get("url", "").strip()
    login = body.get("login", "none")
    if not valid_url(url):
        return jsonify(error="올바른 URL을 입력하세요."), 400
    if login not in LOGIN_CHOICES:
        login = "none"
    platform = detect_platform(url)

    try:
        with yt_dlp.YoutubeDL({**ytdlp_opts(url, login), "skip_download": True}) as ydl:
            data = ydl.extract_info(url, download=False)
    except Exception as e:
        # 사진만 있는 Instagram 게시물은 yt-dlp가 처리하지 못하므로 gallery-dl로 다시 시도
        if platform == "instagram":
            try:
                urls = gallery_dl_urls(url, login)
                browser_photos.pop(url, None)
                return jsonify(platform=platform, kind="photos", count=len(urls),
                               title="Instagram 게시물", thumbnail=urls[0], heights=[])
            except Exception as e2:
                # 로그인 없이: 브라우저로 게시물을 열고 '다음'을 넘기며 사진을 모은다
                try:
                    data = browser_photo_urls(url)
                    browser_photos[url] = data
                    title = f"{data['user']} 인스타 게시물" if data.get("user") else "Instagram 게시물"
                    if data.get("videos"):
                        title += f" (영상 {data['videos']}개는 제외)"
                    return jsonify(platform=platform, kind="photos", count=len(data["images"]),
                                   title=title, thumbnail=data["images"][0], heights=[])
                except Exception as e3:
                    return jsonify(error=friendly_error(f"{e} / {e2} / {e3}", platform)), 400
        return jsonify(error=f"정보를 가져오지 못했습니다: {friendly_error(str(e), platform)}"), 400

    entries = [e for e in (data.get("entries") or []) if e]
    items = entries or [data]
    heights = sorted(
        {f["height"] for it in items for f in (it.get("formats") or [])
         if f.get("height") and f.get("vcodec") != "none"},
        reverse=True,
    )
    return jsonify(
        platform=platform,
        kind="multi" if entries else "video",
        count=len(entries),
        title=data.get("title") or (entries[0].get("title") if entries else ""),
        uploader=data.get("uploader") or data.get("channel"),
        duration=data.get("duration"),
        thumbnail=data.get("thumbnail") or (entries[0].get("thumbnail") if entries else None),
        heights=heights,
    )


def zip_folder(folder, name):
    """폴더 안의 파일을 ZIP 하나로 묶어 임시 위치에 저장하고 경로를 돌려줌."""
    zpath = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as zf:  # 미디어는 이미 압축돼 있음
        for f in sorted(os.listdir(folder)):
            zf.write(os.path.join(folder, f), f)
    return zpath, f"{name}.zip"


def done_saved(job_id, saved, **extra):
    """채널백업에 바로 정리한 경우: 브라우저로 다시 내려받지 않고 저장 위치만 알려 준다."""
    root = channels.load()["root"]
    rel = os.path.relpath(saved, os.path.dirname(root)) if saved.startswith(root) else saved
    update_job(job_id, status="done", percent=100, filepath=saved, filename=os.path.basename(saved),
               saved_to=saved, saved_rel=rel, **extra)


def finish_with_files(job_id, files, folder, name, **extra):
    """파일 1개면 그대로, 여러 개면 ZIP으로 내려주도록 작업 상태를 완료로 바꿈."""
    if len(files) == 1:
        update_job(job_id, status="done", percent=100, filepath=files[0],
                   filename=os.path.basename(files[0]), **extra)
    else:
        zpath, zname = zip_folder(folder, name)
        update_job(job_id, status="done", percent=100, filepath=zpath, filename=zname,
                   count=len(files), folder=folder, **extra)


def run_ytdlp(job_id, url, mode, quality, login, kind):
    def progress_hook(d):
        idx = d.get("info_dict", {}).get("playlist_index")
        total_items = d.get("info_dict", {}).get("n_entries")
        item = f"{idx}/{total_items}" if idx and total_items else None
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            pct = d.get("downloaded_bytes", 0) / total * 100 if total else 0
            update_job(job_id, status="downloading", percent=round(pct, 1), item=item,
                       speed=d.get("_speed_str", "").strip(), eta=d.get("_eta_str", "").strip())
        elif d["status"] == "finished":
            update_job(job_id, status="processing", percent=100, item=item)

    def pp_hook(d):
        if d["status"] == "started":
            update_job(job_id, status="processing")

    if kind == "multi":
        # 여러 항목은 게시물별 폴더에 순서대로 저장
        folder = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4().hex[:8]}_tmp")
        tmpl = os.path.join(folder, "%(playlist_index)02d [%(id)s].%(ext)s")
    else:
        folder = DOWNLOAD_DIR
        # 화질을 파일명에 넣어야 같은 영상을 다른 화질로 받을 때 이전 파일이 재사용되지 않음
        tmpl = os.path.join(DOWNLOAD_DIR, "%(title).60B [%(id)s].%(ext)s" if mode == "audio"
                            else "%(title).60B [%(id)s] %(height)sp.%(ext)s")

    opts = {
        **ytdlp_opts(url, login),
        "outtmpl": tmpl,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [pp_hook],
        "windowsfilenames": True,
    }
    if mode == "audio":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    else:
        h = f"[height<={int(quality)}]" if str(quality).isdigit() else ""
        opts["format"] = f"bv*{h}+ba/b{h}/bv*+ba/b"
        # 해상도를 최우선으로, 같은 해상도면 호환성 좋은 H.264/AAC와 일반 HTTPS 전송을 선호
        opts["format_sort"] = ["res", "vcodec:h264", "acodec:m4a", "proto:https"]
        opts["merge_output_format"] = "mp4"

    platform = detect_platform(url)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(url, download=True)
        items = [e for e in (data.get("entries") or []) if e] or [data]
        files = [rd["filepath"] for it in items for rd in (it.get("requested_downloads") or [])]
        if not files:
            raise RuntimeError("받은 파일이 없습니다.")
        organize = channels.enabled()
        if kind == "multi":
            # 임시 폴더 이름을 게시물 제목으로 바꿔 downloads 에 남김
            name = sanitize_filename(f"{data.get('title') or platform} [{data.get('id')}]")
            final = os.path.join(DOWNLOAD_DIR, name)
            if os.path.exists(final):
                final = f"{final}_{uuid.uuid4().hex[:4]}"
            os.replace(folder, final)
            if organize:   # 채널백업\채널_날짜_사진 [아이디]_01.jpg …
                saved = channels.place_folder(final, channels.channel_of(data, platform), platform,
                                              channels.date_of(data), f"{data.get('title') or ''} [{data.get('id')}]",
                                              "사진")
                done_saved(job_id, saved, count=len(files))
            else:
                files = [os.path.join(final, os.path.basename(f)) for f in files]
                finish_with_files(job_id, files, final, name)
        else:
            res = data.get("resolution") if mode != "audio" else None
            if organize:   # 채널백업\채널_2026-10-20_[Shorts_]제목 [아이디].mp4
                saved = channels.place_file(files[0], items[0], url, platform,
                                            audio=(mode == "audio"))
                done_saved(job_id, saved, resolution=res)
            else:
                finish_with_files(job_id, files, folder, "", resolution=res)
    except Exception as e:
        if kind == "multi":
            shutil.rmtree(folder, ignore_errors=True)  # 실패한 임시 폴더는 남기지 않음
        update_job(job_id, status="error", error=friendly_error(str(e), platform))


def run_browser_photos(job_id, url, data):
    """브라우저로 모은 인스타 사진 주소를 받아서 1장이면 그대로, 여러 장이면 ZIP으로."""
    code = (re.search(r"/(?:p|reel|tv)/([\w-]+)", url) or [None, uuid.uuid4().hex[:8]])[1]
    name = sanitize_filename(f"Post by {data.get('user') or 'instagram'} [{code}]")
    folder = os.path.join(DOWNLOAD_DIR, f"{name}_{uuid.uuid4().hex[:4]}")
    os.makedirs(folder, exist_ok=True)
    urls = data["images"]
    files = []
    update_job(job_id, status="downloading", percent=0, item=f"0/{len(urls)}")
    try:
        for i, u in enumerate(urls, 1):
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.instagram.com/"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                ext = {"image/png": ".png", "image/webp": ".webp"}.get(resp.headers.get_content_type(), ".jpg")
                path = os.path.join(folder, f"{i:02d} [{code}]{ext}")
                with open(path, "wb") as f:
                    shutil.copyfileobj(resp, f)
            files.append(path)
            update_job(job_id, percent=round(i / len(urls) * 100, 1), item=f"{i}/{len(urls)}")
        if channels.enabled():   # 채널백업\계정_날짜_사진 [코드]_01.jpg …
            saved = channels.place_folder(folder, data.get("user") or "instagram", "instagram",
                                          time.strftime("%Y-%m-%d"), code, "사진")
            done_saved(job_id, saved, count=len(files))
        elif len(files) == 1:   # 한 장이면 폴더 없이 파일만
            single = os.path.join(DOWNLOAD_DIR, f"{name}{os.path.splitext(files[0])[1]}")
            os.replace(files[0], single)
            shutil.rmtree(folder, ignore_errors=True)
            finish_with_files(job_id, [single], DOWNLOAD_DIR, "")
        else:
            finish_with_files(job_id, files, folder, name)
        browser_photos.pop(url, None)
    except Exception as e:
        shutil.rmtree(folder, ignore_errors=True)
        update_job(job_id, status="error", error=f"사진을 받지 못했습니다: {e}")


def run_gallery_dl(job_id, url, login, expected):
    platform = detect_platform(url)
    folder = os.path.join(DOWNLOAD_DIR, f"{platform}_{uuid.uuid4().hex[:8]}")
    os.makedirs(folder, exist_ok=True)
    update_job(job_id, status="downloading", percent=0, item=f"0/{expected}" if expected else None)
    try:
        proc = subprocess.Popen(gallery_dl_cmd(login) + ["-D", folder, url], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        done = 0
        for line in proc.stdout:  # gallery-dl은 받은 파일 경로를 한 줄씩 출력함
            if line.strip():
                done += 1
                pct = done / expected * 100 if expected else 0
                update_job(job_id, percent=round(min(pct, 100), 1),
                           item=f"{done}/{expected}" if expected else f"{done}개")
        err = proc.stderr.read()
        proc.wait()
        files = sorted(os.path.join(folder, f) for f in os.listdir(folder))
        if not files:
            raise RuntimeError(err.strip() or "받은 파일이 없습니다.")
        finish_with_files(job_id, files, folder, os.path.basename(folder))
    except Exception as e:
        shutil.rmtree(folder, ignore_errors=True)  # 실패한 임시 폴더는 남기지 않음
        update_job(job_id, status="error", error=friendly_error(str(e), platform))


@app.post("/api/download")
def download():
    body = request.get_json(silent=True) or {}
    url = body.get("url", "").strip()
    mode = body.get("mode", "video")
    quality = body.get("quality", "best")
    kind = body.get("kind", "video")
    login = body.get("login", "none")
    if not valid_url(url):
        return jsonify(error="올바른 URL을 입력하세요."), 400
    if login not in LOGIN_CHOICES:
        login = "none"
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {"status": "queued", "percent": 0}
    if kind == "photos" and url in browser_photos:
        target, args = run_browser_photos, (job_id, url, browser_photos[url])
    elif kind == "photos":
        target, args = run_gallery_dl, (job_id, url, login, int(body.get("count") or 0))
    else:
        target, args = run_ytdlp, (job_id, url, mode, quality, login, kind)
    threading.Thread(target=target, args=args, daemon=True).start()
    return jsonify(job_id=job_id)


@app.get("/api/channels")
def channel_list():
    return jsonify(channels.summary())


@app.post("/api/organize")
def organize_toggle():
    body = request.get_json(silent=True) or {}
    return jsonify(enabled=channels.set_enabled(body.get("enabled", True)))


@app.post("/api/reveal")
def reveal():
    """저장된 파일(또는 채널백업 폴더)을 탐색기에서 연다 — 채널백업 안쪽만."""
    body = request.get_json(silent=True) or {}
    target = body.get("path") or ""
    if body.get("job_id"):
        with jobs_lock:
            target = (jobs.get(body["job_id"]) or {}).get("saved_to", "")
    root = os.path.abspath(channels.load()["root"])
    target = os.path.abspath(target or root)
    if not (target == root or target.startswith(root + os.sep)) or not os.path.exists(target):
        return jsonify(error="열 수 없는 위치입니다."), 400
    subprocess.Popen(["explorer.exe", "/select,", target] if os.path.isfile(target) else ["explorer.exe", target])
    return jsonify(ok=True)


@app.get("/api/progress/<job_id>")
def progress(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            abort(404)
        return jsonify({k: v for k, v in job.items() if k not in ("filepath", "folder")})


@app.get("/api/file/<job_id>")
def get_file(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        abort(404)
    return send_file(job["filepath"], as_attachment=True, download_name=job["filename"])


if __name__ == "__main__":
    # 내 PC에서만 접속 가능하도록 127.0.0.1에만 바인딩
    print("브라우저에서 http://127.0.0.1:5000 을 여세요. 사용하는 동안 이 창을 닫지 마세요.")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
