"""인스타 사진 게시물의 사진 주소를 로그인 없이 모은다 (Playwright 브라우저 사용, 선택 기능).

    <Playwright 가 깔린 python> ig_browser.py <게시물 주소>   → JSON 한 줄 출력

게시물 안의 '다음' 버튼을 화면 안쪽에서 눌러 가며 좌우로 넘기는 사진을 모두 모으고,
프로필 사진·'다른 게시물' 썸네일은 빼고, 원본 크기 주소만 돌려준다.
"""
import json
import re
import sys

from playwright.sync_api import sync_playwright

SLIDES = """() => {
  const main = document.querySelector('main') || document;
  const ul = main.querySelector('ul');
  const out = [];
  if (ul) {
    for (const li of ul.children) {
      const v = li.querySelector('video'), im = li.querySelector('img');
      if (v) out.push({kind: 'video', src: v.poster || ''});
      else if (im) out.push({kind: 'img', src: im.currentSrc || im.src});
    }
  }
  if (!out.length) {
    const big = [...main.querySelectorAll('img')].find(i => i.naturalWidth >= 600);
    if (big) out.push({kind: 'img', src: big.currentSrc || big.src});
  }
  return out;
}"""
NEXT = """() => {
  const b = document.querySelector('main button[aria-label="다음"], main button[aria-label="Next"]');
  if (!b) return false; b.click(); return true;
}"""


def main(url):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 1000}, locale="ko-KR").new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        title = page.evaluate("() => (document.querySelector('meta[property=\"og:title\"]') || {}).content || ''")
        user = re.match(r"\s*(.+?)\s+(?:on Instagram|님의? Instagram|- Instagram)", title or "")
        if not user:   # 사진 설명(alt)에서: "Photo by 계정 on …" / "계정님의 …"
            alt = page.evaluate("() => { const i = [...(document.querySelector('main') || document).querySelectorAll('img')]"
                                ".find(i => i.naturalWidth >= 600); return i ? i.alt : ''; }")
            user = re.search(r"(?:Photo|Video) (?:by|shared by) ([\w.]+)", alt or "") or re.match(r"\s*([\w.]+)님", alt or "")
        seen, images, videos = set(), [], 0
        for _ in range(40):
            for s in page.evaluate(SLIDES):
                if not s["src"] or s["src"] in seen:
                    continue
                seen.add(s["src"])
                if s["kind"] == "img":
                    images.append(s["src"])
                else:
                    videos += 1
            if not page.evaluate(NEXT):
                break
            page.wait_for_timeout(650)
        browser.close()
    print(json.dumps({"images": images, "videos": videos, "user": user.group(1) if user else ""}, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1])
