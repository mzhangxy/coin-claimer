import os
import asyncio
from playwright.async_api import async_playwright
import hcaptcha_challenger as solver

# 从 GitHub Secrets 获取环境变量
TARGET_URL = "https://bot-hosting.net/panel/earn"
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
PROXY_SERVER = os.environ.get("PROXY_SERVER", "")

async def inject_token_and_login(context):
    """
    通过访问主域名并注入 localStorage 来实现免密登录
    """
    page = await context.new_page()
    print("[状态] 正在初始化登录状态...")
    try:
        # 这里也加上超时捕获，放宽判定条件为 domcontentloaded
        await page.goto("https://bot-hosting.net/", wait_until="domcontentloaded", timeout=60000) 
        await page.evaluate(f"window.localStorage.setItem('token', '{AUTH_TOKEN}');")
        print("[状态] Token 注入完成。")
    except Exception as e:
        print(f"[错误] 注入 Token 时访问主页失败: {e}")
        await page.screenshot(path="debug_00_inject_token_error.png")
    return page

async def main():
    if not AUTH_TOKEN:
        print("[错误] 未找到 AUTH_TOKEN 环境变量，脚本终止。")
        return

    async with async_playwright() as p:
        # 1. 启动配置：必须为 Headless 模式，加入代理
        launch_args = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        }
        if PROXY_SERVER:
            print(f"[状态] 检测到代理配置，正在挂载代理...")
            launch_args["proxy"] = {"server": PROXY_SERVER}

        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # 2. 注入凭证并获取页面对象
        page = await inject_token_and_login(context)
        
        print(f"[状态] 正在跳转至目标收集页面: {TARGET_URL}")
        try:
            # 优化 1：将 networkidle 改为 domcontentloaded，只要基础 DOM 加载完就算成功
            # 优化 2：将超时时间增加到 60 秒
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
            # 优化 3：强制等待 5 秒，让页面的动态元素（比如 Cloudflare 的盾或 js 渲染）跑完
            await asyncio.sleep(5)
            await page.screenshot(path="debug_01_after_login.png", full_page=True)
        except Exception as e:
            print(f"[致命错误] 访问收集页面超时或失败: {e}")
            # 优化 4：核心！即使超时报错，也要强行截图，这样 Artifacts 就能收集到“死因”截图
            await page.screenshot(path="debug_01_timeout_error.png", full_page=True)
            await browser.close()
            return

        # 3. 初始化 hCaptcha 挑战者
        challenger = solver.new_challenger(page)

        # 4. 核心收集循环 (每日 10 次上限)
        for i in range(1, 11):
            print(f"\n--- [流程] 开始第 {i}/10 次收集循环 ---")

            # 步骤 A: 检查是否处于冷却时间
            cooldown_count = await page.locator("text=You are on cooldown!").count()
            if cooldown_count > 0:
                print("[中止] 检测到处于冷却时间，任务结束。")
                await page.screenshot(path=f"debug_cooldown_loop_{i}.png", full_page=True)
                break

            # 步骤 B: 检查 hCaptcha
            await asyncio.sleep(2) # 给页面元素一点加载时间
            hcaptcha_iframe = await page.locator("iframe[src*='hcaptcha.com']").count()

            if hcaptcha_iframe > 0:
                print("[动作] 发现 hCaptcha，准备发起挑战...")
                await page.screenshot(path=f"debug_hcaptcha_before_loop_{i}.png")
                
                if await challenger.handle_checkbox():
                    result = await challenger.solve()
                    if result == solver.STATUS_SUCCESS:
                        print("[成功] hCaptcha 挑战通过！")
                        await page.screenshot(path=f"debug_hcaptcha_success_loop_{i}.png")
                    else:
                        print("[失败] hCaptcha 挑战未通过。")
                        await page.screenshot(path=f"debug_hcaptcha_failed_loop_{i}.png")
                        break # 挑战失败，立即中断当前流程并保存截图分析
            else:
                print("[状态] 未发现 hCaptcha。")

            # 步骤 C: 点击绿色 Claim 按钮
            print("[动作] 尝试点击 Claim 按钮...")
            try:
                claim_button = page.locator("button:has-text('Click here to claim'), .btn-success").first
                await claim_button.click(timeout=5000)
                await asyncio.sleep(2)
                await page.screenshot(path=f"debug_after_claim_click_loop_{i}.png")
            except Exception as e:
                print(f"[错误] 无法找到或点击 Claim 按钮: {e}")
                await page.screenshot(path=f"debug_claim_error_loop_{i}.png")
                break

            # 步骤 D: 处理广告弹窗
            try:
                # 尝试匹配带有 X 文本的按钮或关闭类
                close_ad_btn = page.locator("button:has-text('X'), .close").first
                await close_ad_btn.click(timeout=3000)
                print("[动作] 已关闭广告弹窗。")
            except Exception:
                print("[状态] 未检测到广告弹窗或自动跳过。")

            # 步骤 E: 等待进度条
            print("[等待] 正在等待进度条 (预设 20 秒)...")
            await asyncio.sleep(20)
            await page.screenshot(path=f"debug_after_progressbar_loop_{i}.png")

            # 步骤 F: 确认 Success 弹窗
            try:
                ok_button = page.locator("button:has-text('OK')").first
                await ok_button.click(timeout=5000)
                print(f"[成功] 第 {i} 次金币收集闭环完成！")
            except Exception as e:
                print(f"[警告] 未检测到 Success 的 OK 按钮: {e}")
                await page.screenshot(path=f"debug_missing_ok_loop_{i}.png")

            await asyncio.sleep(3)

        print("\n[结束] 脚本执行完毕，准备关闭浏览器。")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
