# -*- coding: utf-8 -*-
"""assets 미디어 생성기 — demo.gif(터미널 데모 애니메이션) + social_preview.png(1280x640).
재현: python assets/_gen_media.py  (의존: Pillow · Windows 폰트 malgun)
데모 텍스트는 합성 예시(PII 0) — tree scan CLEAN 유지."""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
F = "C:/Windows/Fonts/malgun.ttf"
FB = "C:/Windows/Fonts/malgunbd.ttf"

BG, CARD, LINEBG = "#0F172A", "#1E293B", "#0B1220"
TXT, DIM, MINT, BLUE, AMBER = "#E2E8F0", "#94A3B8", "#34D399", "#60A5FA", "#FBBF24"


def font(sz, bold=False):
    return ImageFont.truetype(FB if bold else F, sz)


# ── demo.gif ────────────────────────────────────────────────────────
W, H = 860, 470
LINES = [
    ("$ binggu preview", "cmd", 900),
    ("후보 2건 — 저장하려면 SAVE <번호>", "dim", 700),
    ("  [1] (내 말)  결제 모듈은 스테이징에서 먼저 검증한다", "txt", 650),
    ("  [2] (AI 말)  이번 버그 원인은 타임존 미변환", "txt", 900),
    ("", "txt", 250),
    ('$ binggu save --confirm "SAVE 1,2"', "cmd", 900),
    ("저장 2건 완료 — 내가 승인한 것만 · 자동 저장 없음", "ok", 1100),
    ("", "txt", 250),
    ('$ binggu recall "결제 배포"', "cmd", 900),
    ("관련 기억 1건 · 지난 실수 0건", "dim", 700),
    ("  → 결제 모듈은 스테이징에서 먼저 검증한다 (내 판단)", "mint", 2600),
]
COLOR = {"cmd": TXT, "dim": DIM, "txt": TXT, "ok": MINT, "mint": MINT}


def frame(upto):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    # 창 타이틀바
    d.rounded_rectangle([8, 8, W - 8, H - 8], 16, fill=LINEBG, outline="#334155", width=2)
    d.rectangle([10, 10, W - 10, 52], fill=CARD)
    for i, c in enumerate(("#F87171", "#FBBF24", "#34D399")):
        d.ellipse([26 + i * 26, 24, 40 + i * 26, 38], fill=c)
    d.text((W // 2, 31), "빙구팩 — 내 PC 기억 장부", font=font(15), fill=DIM, anchor="mm")
    y = 78
    f16 = font(17)
    for text, kind, _dur in LINES[:upto]:
        if text.startswith("$ "):
            d.text((34, y), "$", font=font(17, True), fill=MINT)
            d.text((52, y), text[2:], font=f16, fill=COLOR[kind])
        elif text:
            d.text((34, y), text, font=f16, fill=COLOR[kind])
        y += 31
    # 커서
    if upto < len(LINES):
        d.rectangle([34, y + 3, 44, y + 22], fill=MINT)
    return im


def gen_gif():
    frames = [frame(i) for i in range(1, len(LINES) + 1)]
    durs = [dur for _t, _k, dur in LINES]
    frames[0].save(os.path.join(HERE, "demo.gif"), save_all=True,
                   append_images=frames[1:], duration=durs, loop=0, optimize=True)
    print("demo.gif OK (%d frames)" % len(frames))


# ── social_preview.png (1280x640) ───────────────────────────────────
def gen_social():
    W2, H2 = 1280, 640
    im = Image.new("RGB", (W2, H2), BG)
    d = ImageDraw.Draw(im)
    # 미묘한 그라데이션(세로 밴드)
    for i in range(H2):
        t = i / H2
        d.line([(0, i), (W2, i)], fill=(15 + int(15 * t), 23 + int(18 * t), 42 + int(17 * t)))
    # 로고 카드
    lx, ly, ls = 96, 176, 288
    d.rounded_rectangle([lx, ly, lx + ls, ly + ls], 56, fill=LINEBG, outline="#334155", width=4)
    for i, w in enumerate((66, 52, 66)):
        yy = ly + 60 + i * 40
        d.rounded_rectangle([lx + 44, yy, lx + 44 + w, yy + 9], 5, fill="#475569")
    d.rounded_rectangle([lx + 44, ly + 190, lx + 122, ly + 199], 5, fill=MINT)
    pts = {"a": (lx + 186, ly + 66), "b": (lx + 232, ly + 118), "c": (lx + 180, ly + 168), "e": (lx + 238, ly + 206)}
    for p, q in (("a", "b"), ("b", "c"), ("b", "e"), ("c", "e")):
        d.line([pts[p], pts[q]], fill=BLUE, width=5)
    d.ellipse([pts["a"][0] - 14, pts["a"][1] - 14, pts["a"][0] + 14, pts["a"][1] + 14], fill=BLUE)
    d.ellipse([pts["b"][0] - 19, pts["b"][1] - 19, pts["b"][0] + 19, pts["b"][1] + 19], fill=MINT)
    d.ellipse([pts["c"][0] - 13, pts["c"][1] - 13, pts["c"][0] + 13, pts["c"][1] + 13], fill=AMBER)
    d.ellipse([pts["e"][0] - 14, pts["e"][1] - 14, pts["e"][0] + 14, pts["e"][1] + 14], fill=BLUE)
    # 타이포
    d.text((448, 218), "빙구팩", font=font(88, True), fill="#F1F5F9")
    d.text((730, 244), "BingguPack", font=font(62, True), fill=MINT)
    d.text((452, 348), "AI와 일하며 쌓이는 내 판단·실수·취향을", font=font(34), fill="#CBD5E1")
    d.text((452, 398), "내 PC 안의 기억 장부로", font=font(34), fill="#CBD5E1")
    pills = [("자동 저장 없음", "#064E3B", MINT), ("내가 승인한 것만", "#1E3A5F", "#93C5FD"), ("로컬 우선", "#3F2D12", "#FCD34D")]
    x = 452
    for label, bg, fg in pills:
        f22 = font(24, True)
        tw = d.textlength(label, font=f22)
        d.rounded_rectangle([x, 470, x + tw + 44, 522], 26, fill=bg)
        d.text((x + 22, 483), label, font=f22, fill=fg)
        x += tw + 44 + 22
    im.save(os.path.join(HERE, "social_preview.png"))
    print("social_preview.png OK")


if __name__ == "__main__":
    gen_gif()
    gen_social()
