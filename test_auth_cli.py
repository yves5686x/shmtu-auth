"""
校园网认证独立测试脚本（无 GUI）

用途：在命令行单独测试「学号 + 密码 + 验证码」能否认证成功，
     排除 GUI / 凭据服务 / OCR 等干扰因素。测通后再把结论同步回 GUI。

用法（在仓库根目录执行）:
  python test_auth_cli.py                       # 交互式输入学号密码
  python test_auth_cli.py 1852xxxx password     # 命令行直接给账号密码
  python test_auth_cli.py --check               # 只测联网状态 + 门户探测，不登录
  python test_auth_cli.py --manual              # 不用 OCR，每轮验证码弹到文件人工输入
  python test_auth_cli.py --service "校园网"     # 强制 service（有线=校园网, 无线=iSMU）
  python test_auth_cli.py --qs "<认证页URL或queryString>"   # 手动指定 queryString

依赖: requests, urllib3<2, loguru（仓库 requirements 已含）; ddddocr 可选。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

if os.name == "nt" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import argparse  # noqa: E402


def build_captcha_provider(force_manual: bool):
    """验证码回调：优先 ddddocr 自动识别，失败/不可用时落到人工输入。"""

    ocr = None
    if not force_manual:
        try:
            import ddddocr

            ocr = ddddocr.DdddOcr(show_ad=False)
            print("[captcha] 已启用 ddddocr 自动识别")
        except Exception as e:  # noqa: BLE001
            print(f"[captcha] ddddocr 不可用（{e}），将改为人工输入验证码")

    def provider(image: bytes):
        if ocr is not None:
            try:
                code = ocr.classification(image)
                if code and code.strip():
                    print(f"[captcha] OCR 识别结果: {code.strip()}")
                    return code.strip()
            except Exception as e:  # noqa: BLE001
                print(f"[captcha] OCR 识别异常: {e}")

        # 人工输入：把验证码图存到当前目录，用看图软件打开后照着输
        captcha_path = os.path.join(os.getcwd(), "captcha.png")
        with open(captcha_path, "wb") as f:
            f.write(image)
        print(f"[captcha] 验证码已保存到: {captcha_path}")
        try:
            if os.name == "nt":
                os.startfile(captcha_path)  # noqa: S606 Windows 自动打开看图
        except Exception:  # noqa: BLE001
            pass
        try:
            code = input("[captcha] 请输入图片中的 4 位验证码（直接回车=放弃本轮）: ").strip()
        except EOFError:
            return None
        return code or None

    return provider


def main() -> int:
    parser = argparse.ArgumentParser(description="校园网认证独立测试脚本（无 GUI）")
    parser.add_argument("user", nargs="?", help="学号")
    parser.add_argument("password", nargs="?", help="密码（明文）")
    parser.add_argument("--check", action="store_true", help="只测联网状态与门户探测，不登录")
    parser.add_argument("--manual", action="store_true", help="禁用 OCR，验证码改为人工输入")
    parser.add_argument("--service", default="", help='强制 service 提交值，如 "校园网" 或 "iSMU"')
    parser.add_argument("--qs", default="", help="手动指定 queryString（认证页 URL 或裸 queryString）")
    args = parser.parse_args()

    if args.service:
        os.environ["SHMTU_AUTH_PORTAL_SERVICE"] = args.service
        print(f"[config] 已强制 service = {args.service}")
    if args.qs:
        os.environ["SHMTU_AUTH_QUERY_STRING"] = args.qs
        print("[config] 已启用手动 queryString，跳过自动探测")

    from shmtu_auth.src.core.core import ShmtuNetAuthCore

    core = ShmtuNetAuthCore()

    print("\n[1/2] 检测联网状态 ...")
    online = core.test_net()
    print(f"      联网状态: {'已联网（无需认证）' if online else '未认证'}")
    if args.check:
        return 0 if online else 2

    user = args.user or input("请输入学号: ").strip()
    pwd = args.password or input("请输入密码: ").strip()
    if not user or not pwd:
        print("学号或密码为空，退出")
        return 1

    print(f"\n[2/2] 开始认证（学号 {user[:4]}****）...")
    ok, msg = core.login(
        user,
        pwd,
        password_encrypt=False,
        skip_network_check=False,
        captcha_provider=build_captcha_provider(args.manual),
    )

    print("\n" + "=" * 50)
    if ok:
        print("✅ 认证成功！")
        print(f"   {msg}")
        print("   结论：账号密码没问题，GUI 侧可以直接同步该账号使用。")
        return 0

    print("❌ 认证失败")
    print(f"   原因: {msg}")
    if "用户不存在或者密码错误" in msg:
        print("   → 门户确认收到请求但校验失败。若 queryString 正常抓到了 mac，")
        print("     这时才真的是账号或密码错误；请确认学号无多余空格、密码输入正确。")
    elif "验证码" in msg:
        print("   → 验证码问题。可加 --manual 改人工输入验证码再试一次。")
    print("=" * 50)
    return 1


if __name__ == "__main__":
    sys.exit(main())
