"""下载桌宠壳 Live2D 渲染依赖库（vendor 三件套，不入库）。

用法：python tools/fetch_vendor.py
目标目录：pet_shell/src/vendor/（已 gitignore，每次克隆后需运行一次；
`npm run dev` / `npm run build` 会通过 predev/prebuild 钩子自动调用本脚本）。

三个文件均为第三方发布物，按 SHA256 校验完整性：
- pixi.min.js            pixi.js@6.5.10（MIT）
- cubism4.min.js         pixi-live2d-display@0.4.0 专用构建（MIT）
- live2dcubismcore.min.js Live2D 官方托管直链（专有许可，仅供随应用使用）

文件已存在且哈希匹配时跳过，可反复运行。
"""

import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request

VENDOR_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "vendor")
)

# 注意：live2dcubismcore 官方直链始终指向最新版；若上游更新导致哈希不匹配，
# 需核对新版本兼容性后更新此处哈希（2026-07 验证：latest = Core 5.1.0）。
FILES = {
    "pixi.min.js": {
        "urls": [
            "https://cdn.jsdelivr.net/npm/pixi.js@6.5.10/dist/browser/pixi.min.js",
            "https://unpkg.com/pixi.js@6.5.10/dist/browser/pixi.min.js",
        ],
        "sha256": "403f2f2ee8145fa17f60c5c89403056efe2680e5096ec2762036486914ed19c5",
    },
    "cubism4.min.js": {
        "urls": [
            "https://cdn.jsdelivr.net/npm/pixi-live2d-display@0.4.0/dist/cubism4.min.js",
            "https://unpkg.com/pixi-live2d-display@0.4.0/dist/cubism4.min.js",
        ],
        "sha256": "af1267e6d52759b245766c578d905bfa025b532d5c3cc727c370957c4409e21b",
    },
    "live2dcubismcore.min.js": {
        "urls": [
            "https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js",
        ],
        "sha256": "25ae938cb4fe282ce189b357bcc97e603d1e1f7ec78bf04150d401c23cdc792f",
    },
}

USER_AGENT = "astrbot-pet-shell-fetch_vendor/1.0"


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_one(name, spec):
    """按候选 URL 顺序尝试下载并校验哈希，成功返回临时文件路径。"""
    errors = []
    for url in spec["urls"]:
        tmp_fd, tmp_path = tempfile.mkstemp(prefix="vendor_", suffix=".js")
        ok = False
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with os.fdopen(tmp_fd, "wb") as out, urllib.request.urlopen(
                req, timeout=60
            ) as resp:
                shutil.copyfileobj(resp, out)
            digest = sha256_of(tmp_path)
            if digest == spec["sha256"]:
                ok = True
                return tmp_path
            errors.append(f"{url} 哈希不匹配（得到 {digest[:12]}…）")
        except Exception as e:  # noqa: BLE001 - 任一源失败都尝试下一个
            errors.append(f"{url} 下载失败：{e}")
        finally:
            if not ok and os.path.exists(tmp_path):
                os.remove(tmp_path)
    raise RuntimeError(f"{name} 全部下载源失败：\n  - " + "\n  - ".join(errors))


def main():
    os.makedirs(VENDOR_DIR, exist_ok=True)
    failed = False
    for name, spec in FILES.items():
        dest = os.path.join(VENDOR_DIR, name)
        if os.path.exists(dest):
            if sha256_of(dest) == spec["sha256"]:
                print(f"[skip] {name} 已存在且哈希匹配")
                continue
            print(f"[warn] {name} 已存在但哈希不匹配，重新下载")
        try:
            tmp_path = download_one(name, spec)
        except RuntimeError as e:
            print(f"[fail] {e}", file=sys.stderr)
            failed = True
            continue
        shutil.move(tmp_path, dest)
        print(f"[ok] {name} -> {dest}")
    if failed:
        print("\n部分文件下载失败，请检查网络连接后重跑本脚本。", file=sys.stderr)
        sys.exit(1)
    print("vendor 依赖就绪。")


if __name__ == "__main__":
    main()
