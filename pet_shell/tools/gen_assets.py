"""生成桌宠占位立绘（8 种情绪）+ 托盘/应用图标。

用法：python tools/gen_assets.py
输出：pet_shell/src/assets/<英文名>.png 与 pet_shell/src-tauri/icons/icon.png
文件名使用英文（Tauri 资产协议对非 ASCII 文件名支持不佳），
情绪中文名 -> 文件名的映射见 EMOTION_FILES，与前端 app.js 中的映射保持一致。
占位图为简单卡通脸，正式使用时可替换 assets/ 下的同名文件。
"""

import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "src", "assets")
ICONS = os.path.join(ROOT, "src-tauri", "icons")

# 情绪 -> (主色, 眼睛样式, 嘴巴样式)
EMOTIONS = {
    "平静": ("#9ecfff", "normal", "smile"),
    "高兴": ("#ffd166", "happy", "open"),
    "生气": ("#ff8a80", "angry", "frown"),
    "害羞": ("#ffb3c7", "shy", "small"),
    "惊讶": ("#c3aed6", "wide", "o"),
    "难过": ("#8fa8c8", "sad", "sad"),
    "疑惑": ("#a8d8b9", "normal", "wavy"),
    "调皮": ("#ffc48f", "wink", "grin"),
}

# 情绪中文名 -> 立绘文件名（英文），与前端 app.js 的 EMOTION_FILES 一致
EMOTION_FILES = {
    "平静": "calm",
    "高兴": "happy",
    "生气": "angry",
    "害羞": "shy",
    "惊讶": "surprised",
    "难过": "sad",
    "疑惑": "confused",
    "调皮": "playful",
}

FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
SIZE = 256


def font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError:
        return ImageFont.load_default()


def draw_face(emotion, color, eyes, mouth):
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆形脸
    d.ellipse([28, 28, SIZE - 28, SIZE - 28], fill=color, outline="#5a4a42", width=5)

    # 眼睛
    ey, r = 110, 13
    lx, rx = 95, 161
    if eyes == "happy":  # 弯月眼
        for cx in (lx, rx):
            d.arc([cx - r, ey - r, cx + r, ey + r], 180, 360, fill="#5a4a42", width=5)
    elif eyes == "angry":
        d.line([lx - r, ey - r, lx + r, ey + 4], fill="#5a4a42", width=5)
        d.line([rx - r, ey + 4, rx + r, ey - r], fill="#5a4a42", width=5)
    elif eyes == "wide":
        for cx in (lx, rx):
            d.ellipse([cx - r - 3, ey - r - 3, cx + r + 3, ey + r + 3], outline="#5a4a42", width=5)
            d.ellipse([cx - 4, ey - 4, cx + 4, ey + 4], fill="#5a4a42")
    elif eyes == "sad":
        d.line([lx - r, ey + 4, lx + r, ey - r], fill="#5a4a42", width=5)
        d.line([rx - r, ey - r, rx + r, ey + 4], fill="#5a4a42", width=5)
        # 泪滴
        d.ellipse([lx - 4, ey + 14, lx + 4, ey + 24], fill="#6fb7ff")
    elif eyes == "wink":
        d.arc([lx - r, ey - r, lx + r, ey + r], 180, 360, fill="#5a4a42", width=5)
        d.ellipse([rx - r, ey - r, rx + r, ey + r], fill="#5a4a42")
    elif eyes == "shy":
        for cx in (lx, rx):
            d.arc([cx - r, ey - r, cx + r, ey + r], 180, 360, fill="#5a4a42", width=5)
        # 腮红
        d.ellipse([lx - 22, ey + 16, lx + 6, ey + 30], fill=(255, 120, 140, 130))
        d.ellipse([rx - 6, ey + 16, rx + 22, ey + 30], fill=(255, 120, 140, 130))
    else:  # normal
        for cx in (lx, rx):
            d.ellipse([cx - r, ey - r, cx + r, ey + r], fill="#5a4a42")

    # 嘴巴
    my = 165
    cx = 128
    if mouth == "smile":
        d.arc([cx - 20, my - 12, cx + 20, my + 14], 20, 160, fill="#5a4a42", width=5)
    elif mouth == "open":
        d.ellipse([cx - 18, my - 8, cx + 18, my + 16], fill="#8c4a3c")
    elif mouth == "frown":
        d.arc([cx - 20, my, cx + 20, my + 24], 200, 340, fill="#5a4a42", width=5)
    elif mouth == "small":
        d.arc([cx - 8, my - 4, cx + 8, my + 8], 20, 160, fill="#5a4a42", width=4)
    elif mouth == "o":
        d.ellipse([cx - 9, my - 4, cx + 9, my + 14], outline="#5a4a42", width=5)
    elif mouth == "sad":
        d.arc([cx - 16, my, cx + 16, my + 20], 200, 340, fill="#5a4a42", width=5)
    elif mouth == "wavy":
        d.line([(cx - 18, my), (cx - 9, my + 6), (cx, my), (cx + 9, my + 6), (cx + 18, my)],
               fill="#5a4a42", width=4)
    elif mouth == "grin":
        d.arc([cx - 20, my - 12, cx + 20, my + 14], 20, 160, fill="#5a4a42", width=5)
        d.line([cx - 14, my + 2, cx + 14, my + 2], fill="#5a4a42", width=4)

    # 底部情绪名
    f = font(26)
    bbox = d.textbbox((0, 0), emotion, font=f)
    tw = bbox[2] - bbox[0]
    d.text(((SIZE - tw) / 2, SIZE - 44), emotion, font=f, fill="#5a4a42")
    return img


def main():
    os.makedirs(ASSETS, exist_ok=True)
    os.makedirs(ICONS, exist_ok=True)
    for emotion, (color, eyes, mouth) in EMOTIONS.items():
        img = draw_face(emotion, color, eyes, mouth)
        out = os.path.join(ASSETS, f"{EMOTION_FILES[emotion]}.png")
        img.save(out)
        print("written:", out)

    icon = draw_face("平静", EMOTIONS["平静"][0], "normal", "smile").resize((512, 512))
    icon_path = os.path.join(ICONS, "icon.png")
    icon.save(icon_path)
    print("written:", icon_path)


if __name__ == "__main__":
    main()
