# -*- coding: utf-8 -*-
"""assets 미디어 생성기 v2 — demo.gif(타이핑 애니메이션 터미널) + social_preview.png(1280x640).
재현: python assets/_gen_media.py  (의존: Pillow · Windows 폰트 malgun)
데모 텍스트는 합성 예시(PII 0) — tree scan CLEAN 유지."""
import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
F = "C:/Windows/Fonts/malgun.ttf"
FB = "C:/Windows/Fonts/malgunbd.ttf"

BG = (11, 18, 32)
TITLE_A, TITLE_B = (27, 42, 71), (17, 27, 48)
TERM = (10, 16, 29)
TXT, DIM, MINT, BLUE, AMBER = (231, 237, 247), (140, 160, 192), (94, 234, 212), (165, 196, 252), (252, 211, 77)


def font(sz, bold=False):
    return ImageFont.truetype(FB if bold else F, sz)


# ── demo.gif — 타이핑 애니메이션 ────────────────────────────────────
W, H = 880, 500
PAD = 26  # 창 그림자 여백
LINES = [
    ("binggu preview", "cmd"),
    ("후보 2건 — 저장하려면 SAVE <번호>", "dim"),
    ("  [1] (내 말)  결제 모듈은 스테이징에서 먼저 검증한다", "txt"),
    ("  [2] (AI 말)  이번 버그 원인은 타임존 미변환", "txt"),
    ("", "txt"),
    ('binggu save --confirm "SAVE 1,2"', "cmd"),
    ("저장 2건 완료 — 내가 승인한 것만 · 자동 저장 없음", "ok"),
    ("", "txt"),
    ('binggu recall "결제 배포"', "cmd"),
    ("관련 기억 1건 · 지난 실수 0건", "dim"),
    ("  → 결제 모듈은 스테이징에서 먼저 검증한다 (내 판단)", "mint"),
]
COLOR = {"cmd": TXT, "dim": DIM, "txt": TXT, "ok": MINT, "mint": MINT}


def base_window():
    """창 배경 + 그림자 + 타이틀바(그라데이션)."""
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    # 그림자
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([PAD + 4, PAD + 10, W - PAD + 4, H - PAD + 10], 22, fill=(0, 0, 0, 150))
    im.paste(Image.new("RGB", (W, H), BG), (0, 0))
    im = Image.alpha_composite(im.convert("RGBA"), sh.filter(ImageFilter.GaussianBlur(10))).convert("RGB")
    d = ImageDraw.Draw(im)
    # 본체
    d.rounded_rectangle([PAD, PAD, W - PAD, H - PAD], 20, fill=TERM, outline=(51, 65, 94), width=2)
    # 타이틀바 그라데이션
    for i in range(44):
        t = i / 44
        c = tuple(int(TITLE_A[k] + (TITLE_B[k] - TITLE_A[k]) * t) for k in range(3))
        y = PAD + 2 + i
        d.line([(PAD + 2, y), (W - PAD - 2, y)], fill=c)
    d.rounded_rectangle([PAD, PAD, W - PAD, PAD + 46], 20, outline=(51, 65, 94), width=2)
    d.rectangle([PAD + 2, PAD + 24, W - PAD - 2, PAD + 46], fill=TERM)
    for i in range(44):
        t = i / 44
        c = tuple(int(TITLE_A[k] + (TITLE_B[k] - TITLE_A[k]) * t) for k in range(3))
        d.line([(PAD + 2, PAD + 2 + i)], fill=c)
    # 다시 그리기(단순화): 타이틀 영역 덮기
    for i in range(44):
        t = i / 44
        c = tuple(int(TITLE_A[k] + (TITLE_B[k] - TITLE_A[k]) * t) for k in range(3))
        d.line([(PAD + 3, PAD + 2 + i), (W - PAD - 3, PAD + 2 + i)], fill=c)
    d.rounded_rectangle([PAD, PAD, W - PAD, H - PAD], 20, outline=(51, 65, 94), width=2)
    d.line([(PAD + 2, PAD + 46), (W - PAD - 2, PAD + 46)], fill=(51, 65, 94), width=2)
    for i, c in enumerate(((248, 113, 113), (251, 191, 36), (52, 211, 153))):
        d.ellipse([PAD + 18 + i * 26, PAD + 16, PAD + 32 + i * 26, PAD + 30], fill=c)
    d.text((W // 2, PAD + 24), "빙구팩 — 내 PC 기억 장부", font=font(15), fill=DIM, anchor="mm")
    return im


BASEWIN = base_window()


def frame(lines_done, typing=None, cursor=True):
    """lines_done=완성 라인 수, typing=(줄 idx, 부분 텍스트)."""
    im = BASEWIN.copy()
    d = ImageDraw.Draw(im)
    f17, fb17 = font(17), font(17, True)
    y = PAD + 66
    rows = list(range(lines_done)) + ([typing[0]] if typing else [])
    for i in rows:
        text, kind = LINES[i]
        shown = typing[1] if (typing and i == typing[0]) else text
        x = PAD + 12
        if kind == "cmd":
            d.text((x, y), "$", font=fb17, fill=MINT)
            d.text((x + 20, y), shown, font=f17, fill=COLOR[kind])
            end = x + 20 + d.textlength(shown, font=f17)
        elif shown:
            d.text((x, y), shown, font=f17, fill=COLOR[kind])
            end = x + d.textlength(shown, font=f17)
        else:
            end = x
        if not (typing and i == typing[0]):
            y += 32
    # 커서
    if cursor:
        cx = (end + 6) if (typing) else PAD + 12
        cy = y if not typing else y
        d.rectangle([cx, cy + 2, cx + 10, cy + 22], fill=MINT)
    return im


def gen_gif():
    frames, durs = [], []

    def add(im, ms):
        frames.append(im)
        durs.append(ms)

    done = 0
    for idx, (text, kind) in enumerate(LINES):
        if kind == "cmd":
            # 프롬프트 커서 대기
            add(frame(done, None, cursor=True), 420)
            # 타이핑(3자 단위)
            for j in range(3, len(text) + 1, 3):
                add(frame(done, (idx, text[:j]), cursor=True), 70)
            add(frame(done, (idx, text), cursor=True), 300)
            done = idx + 1
        else:
            done = idx + 1
            add(frame(done, None, cursor=False), 620 if text else 200)
    # 마지막 정지 + 커서 깜빡 2회
    last = frame(len(LINES), None, cursor=False)
    add(last, 1600)
    blink = frame(len(LINES), None, cursor=False)
    add(blink, 400)
    add(last, 2400)
    frames[0].save(os.path.join(HERE, "demo.gif"), save_all=True,
                   append_images=frames[1:], duration=durs, loop=0, optimize=True)
    print("demo.gif OK (%d frames)" % len(frames))


# ── social_preview.png — 글로우 + 별자리 ────────────────────────────
def gen_social():
    W2, H2 = 1280, 640
    im = Image.new("RGB", (W2, H2), BG)
    d = ImageDraw.Draw(im)
    for i in range(H2):  # 베이스 그라데이션
        t = i / H2
        d.line([(0, i), (W2, i)], fill=(11 + int(10 * t), 18 + int(13 * t), 32 + int(14 * t)))
    # 글로우 레이어(인디고 좌상 · 민트 우하)
    glow = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-260, -300, 560, 340], fill=(56, 72, 150, 90))
    gd.ellipse([820, 330, 1560, 900], fill=(16, 90, 70, 80))
    gd.ellipse([60, 120, 500, 520], fill=(52, 211, 153, 26))
    im = Image.alpha_composite(im.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(90))).convert("RGB")
    d = ImageDraw.Draw(im)
    # 별자리(우측)
    pts = [(1020, 96), (1106, 148), (1064, 236), (1190, 196), (1146, 300), (988, 300)]
    edges = [(0, 1), (1, 2), (1, 3), (2, 4), (3, 4), (2, 5)]
    for a, b in edges:
        d.line([pts[a], pts[b]], fill=(59, 74, 107), width=2)
    star_c = [(139, 156, 248), (94, 234, 212), (148, 163, 184), (148, 163, 184), (252, 211, 77), (148, 163, 184)]
    for (x, y), c in zip(pts, star_c):
        d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=c)
    for x, y, r in [(940, 70, 2), (1240, 110, 3), (1210, 330, 2), (900, 210, 2), (1250, 240, 2)]:
        d.ellipse([x - r, y - r, x + r, y + r], fill=(120, 134, 160))
    # 로고 카드(그림자)
    lx, ly, ls = 96, 176, 288
    shadow = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([lx + 6, ly + 14, lx + ls + 6, ly + ls + 14], 56, fill=(0, 0, 0, 160))
    im = Image.alpha_composite(im.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(14))).convert("RGB")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([lx, ly, lx + ls, ly + ls], 56, fill=(11, 18, 32), outline=(51, 65, 94), width=4)
    for i, w in enumerate((66, 52, 66)):
        yy = ly + 58 + i * 38
        d.rounded_rectangle([lx + 44, yy, lx + 44 + w, yy + 9], 5, fill=(76, 90, 120))
    d.rounded_rectangle([lx + 44, ly + 182, lx + 122, ly + 191], 5, fill=MINT)
    pts2 = {"a": (lx + 186, ly + 62), "b": (lx + 230, ly + 114), "c": (lx + 178, ly + 162), "e": (lx + 236, ly + 202)}
    # 노드 글로우
    ng = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
    ngd = ImageDraw.Draw(ng)
    for p, c in (("a", (96, 165, 250)), ("b", (52, 211, 153)), ("c", (251, 191, 36)), ("e", (96, 165, 250))):
        x, y = pts2[p]
        ngd.ellipse([x - 26, y - 26, x + 26, y + 26], fill=c + (110,))
    im = Image.alpha_composite(im.convert("RGBA"), ng.filter(ImageFilter.GaussianBlur(12))).convert("RGB")
    d = ImageDraw.Draw(im)
    for p, q in (("a", "b"), ("b", "c"), ("b", "e"), ("c", "e")):
        d.line([pts2[p], pts2[q]], fill=(129, 140, 248), width=5)
    for p, c, r in (("a", (96, 165, 250), 14), ("b", (52, 211, 153), 19), ("c", (251, 191, 36), 13), ("e", (96, 165, 250), 14)):
        x, y = pts2[p]
        d.ellipse([x - r, y - r, x + r, y + r], fill=c)
    d.ellipse([pts2["b"][0] - 8, pts2["b"][1] - 8, pts2["b"][0] + 8, pts2["b"][1] + 8], fill=(236, 253, 245))
    # 승인 도장
    d.ellipse([lx + 40, ly + 218, lx + 96, ly + 274], fill=(5, 46, 34), outline=(52, 211, 153), width=4)
    d.line([(lx + 54, ly + 246), (lx + 64, ly + 258), (lx + 84, ly + 232)], fill=(94, 234, 212), width=6, joint="curve")
    # 타이포
    d.text((448, 210), "빙구팩", font=font(92, True), fill=(241, 245, 249))
    d.text((746, 240), "BingguPack", font=font(60, True), fill=(52, 211, 153))
    d.text((452, 352), "AI와 일하며 쌓이는 내 판단·실수·취향을", font=font(34), fill=(203, 213, 225))
    d.text((452, 402), "내 PC 안의 기억 장부로", font=font(34), fill=(203, 213, 225))
    pills = [("자동 저장 없음", (14, 59, 44), (42, 163, 122), MINT),
             ("내가 승인한 것만", (24, 43, 82), (75, 101, 181), (165, 196, 252)),
             ("로컬 우선", (59, 43, 16), (176, 138, 46), (252, 211, 77))]
    x = 452
    for label, bg, br, fg in pills:
        f22 = font(24, True)
        tw = d.textlength(label, font=f22)
        d.rounded_rectangle([x, 474, x + tw + 46, 528], 27, fill=bg, outline=br, width=2)
        d.text((x + 23, 488), label, font=f22, fill=fg)
        x += tw + 46 + 20
    im.save(os.path.join(HERE, "social_preview.png"))
    print("social_preview.png OK")


if __name__ == "__main__":
    gen_gif()
    gen_social()
