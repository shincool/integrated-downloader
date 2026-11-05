"""받은 영상·사진을 한 폴더에 채널 이름이 붙은 파일로 정리한다.

    다운로드\\채널백업\\채널_2026-10-20_제목 [아이디].mp4
    다운로드\\채널백업\\채널_2026-10-21_Shorts_제목 [아이디].mp4
    다운로드\\채널백업\\채널_2026-10-22_사진 [게시물코드]_01.jpg

- 폴더 안에 폴더를 만들지 않는다 (채널·종류는 파일 이름에).
- 저장 위치와 켜고 끄기는 channels.json 에 저장한다 (처음 실행하면 자동으로 생김).
  기본 위치는 내 다운로드 폴더의 '채널백업'. 다른 곳에 두려면 channels.json 의 "root" 를 바꾼다.
"""
import json
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS = HERE / "channels.json"
DEFAULT_ROOT = Path.home() / "Downloads" / "채널백업"
lock = threading.Lock()
TAG = {"Shorts": "Shorts_", "Live": "Live_", "음원": "음원_"}   # 영상·릴스는 표시 없음


def load():
    try:
        s = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    s.setdefault("enabled", True)
    s.setdefault("root", str(DEFAULT_ROOT))
    s.setdefault("channels", {})
    return s


def save(s):
    SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def enabled():
    return load()["enabled"]


def clean(text, limit=80):
    """윈도우 파일 이름에 쓸 수 없는 글자를 빼고 길이를 줄인다."""
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(text or "")).strip()
    t = re.sub(r"\s+", " ", t)[:limit].rstrip(". ")
    return t or "이름없음"


# ---------- 받은 영상에서 알아내기 ----------
def channel_of(info, fallback="기타"):
    for k in ("channel", "uploader", "creator", "uploader_id", "channel_id"):
        v = info.get(k)
        if v:
            return str(v).lstrip("@")
    return fallback


def date_of(info):
    d = info.get("upload_date") or info.get("release_date")
    if d and re.fullmatch(r"\d{8}", str(d)):
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    ts = info.get("timestamp") or info.get("release_timestamp")
    if ts:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    return datetime.now().strftime("%Y-%m-%d")


def kind_of(info, url, platform, audio=False):
    if audio:
        return "음원"
    if info.get("live_status") in ("is_live", "was_live", "post_live"):
        return "Live"
    if platform == "youtube":
        w, h = info.get("width") or 0, info.get("height") or 0
        dur = info.get("duration") or 9999
        if "/shorts/" in (url or "") or "/shorts/" in (info.get("webpage_url") or "") or (h > w and dur <= 180):
            return "Shorts"
    return "영상"


def register(s, name, platform):
    ch = s["channels"].setdefault(name, {"platform": platform, "added": time.time()})
    ch["platform"] = ch.get("platform") or platform
    ch["last"] = time.time()


def free_path(p: Path):
    if not p.exists():
        return p
    i = 2
    while True:
        q = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not q.exists():
            return q
        i += 1


def place_file(src, info, url, platform, audio=False):
    """받은 파일 하나를 '채널_날짜_제목 [아이디]' 이름으로 채널백업 폴더에 옮긴다."""
    with lock:
        s = load()
        name = channel_of(info)
        register(s, name, platform)
        tag = TAG.get(kind_of(info, url, platform, audio), "")
        vid = info.get("id") or ""
        title = clean(info.get("title") or info.get("description") or "", 90)
        fname = f"{clean(name, 40)}_{date_of(info)}_{tag}{title}{f' [{vid}]' if vid else ''}{Path(src).suffix}"
        dst = free_path(Path(s["root"]) / fname)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        save(s)
        return str(dst)


def place_folder(src_folder, channel, platform, date, label, kind="사진"):
    """여러 장짜리 게시물: 폴더를 만들지 않고 '채널_날짜_사진 [코드]_01.jpg' 처럼 펼쳐 넣는다.
    첫 파일 경로를 돌려준다."""
    with lock:
        s = load()
        register(s, channel, platform)
        code = re.search(r"\[([\w-]+)\]", label or "")
        code = code.group(1) if code else clean(label, 30)
        files = sorted(f for f in Path(src_folder).iterdir() if f.is_file())
        first = None
        for i, f in enumerate(files, 1):
            what = "영상" if f.suffix.lower() in (".mp4", ".mov", ".webm") else kind
            dst = free_path(Path(s["root"]) / f"{clean(channel, 40)}_{date}_{what} [{code}]_{i:02d}{f.suffix.lower()}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dst))
            first = first or str(dst)
        shutil.rmtree(src_folder, ignore_errors=True)
        save(s)
        return first or str(Path(s["root"]))


# ---------- 화면용 ----------
def summary():
    s = load()
    root = Path(s["root"])
    files = [f for f in root.iterdir() if f.is_file()] if root.is_dir() else []
    per = {}
    for name in s["channels"]:
        prefix = clean(name, 40) + "_"
        per[name] = sum(1 for f in files if f.name.startswith(prefix))
    chans = sorted(({"name": n, "platform": s["channels"][n].get("platform", ""), "count": c,
                     "last": s["channels"][n].get("last", 0)} for n, c in per.items() if c),
                   key=lambda x: -x["last"])
    return {"root": s["root"], "enabled": s["enabled"], "files": len(files),
            "size": sum(f.stat().st_size for f in files), "channels": chans}


def set_enabled(on):
    with lock:
        s = load()
        s["enabled"] = bool(on)
        save(s)
        return s["enabled"]
