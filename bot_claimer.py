import os
import asyncio
from playwright.async_api import async_playwright

# 导入库，利用双重保险防止 API 报错卡死主流程
try:
    import hcaptcha_challenger as solver
except ImportError:
    solver = None

# 从 GitHub Secrets 获取环境变量
TARGET_URL = "https://bot-hosting.net/panel/earn"
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
# 现在的代理变量支持用逗号或换行符分隔的多个代理
RAW_PROXIES = os.environ.get("PROXY_SERVER", "")

def get_proxy_list():
    """解析环境变量中的代理列表"""
    if not RAW_PROXIES:
        return []
    # 支持换行或逗号分隔
    proxies = RAW_PROXIES.replace('\n', ',').split(',')
    return [p.strip() for p in proxies if p.strip()]

async def get_working_proxy(p, proxy_list):
    """测试并返回第一个可用的代理"""
    print(f"[状态] 发现 {len(proxy_list)} 个备选代理，开始快速可用性检测...")
    for proxy in proxy_list:
        print(f"[检测] 正在测试代理: {proxy}")
        try:
            # 启动一个临时的无头浏览器进行测试
            browser = await p.chromium.launch(headless=True, proxy={"server": proxy})
            context = await browser.new_context()
            page = await context.new_page()
            
            # 使用较短的超时时间 (15秒) 并且只等待 commit (收到响应头)，极大加快检测速度
            response = await page.goto("https://bot-hosting.net/", timeout=15000, wait_until="commit")
            
            if response and response.status == 200:
                print(f"[成功] 代理连通性良好: {proxy}")
                await browser.close()
                return proxy
            else:
                print(f"[警告] 代理连通，但返回状态码异常: {response.status if response else 'None'}")
                await browser.close()
        except Exception as e:
            print(f"[失败] 代理超时或无法连接: {e}")
            try:
                await browser.close()
            except Exception:
                pass
            
    print("[致命错误] 代理池中所有代理均检测失败！")
    return None

async def inject_token_and_login(context):
    page = await context.new_page()
    
    # 注入底层 Stealth 脚本，增强环境伪装
    stealth_js = """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.navigator.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    """
    await context.add_init_script(stealth_js)
    
    print("[状态] 正在初始化登录状态并注入底层伪装防护...")
    try:
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

    proxy_list = get_proxy_list()

    async with async_playwright() as p:
        working_proxy = None
        # 如果配置了代理池，先找出健康的代理
        if proxy_list:
            working_proxy = await get_working_proxy(p, proxy_list)
            if not working_proxy:
                print("[中止] 没有可用代理，放弃本次任务。")
                return

        # 启动主业务配置
        launch_args = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        }
        
        if working_proxy:
            print(f"[状态] 主流程将使用验证通过的代理: {working_proxy}")
            launch_args["proxy"] = {"server": working_proxy}
        elif not proxy_list:
             print(f"[状态] 未配置代理，将使用直连网络运行。")

        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        page = await inject_token_and_login(context)
        
        print(f"[状态] 正在跳转至目标收集页面: {TARGET_URL}")
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(5)
            await page.screenshot(path="debug_01_after_login.png", full_page=True)
        except Exception as e:
            print(f"[致命错误] 访问收集页面超时: {e}")
            await page.screenshot(path="debug_01_timeout_error.png", full_page=True)
            await browser.close()
            return

        # 核心收集循环 (每日 10 次上限)
        for i in range(1, 11):
            print(f"\n--- [流程] 开始第 {i}/10 次收集循环 ---")

            # 步骤 A: 检查冷却状态
            cooldown_count = await page.locator("text=You are on cooldown!").count()
            if cooldown_count > 0:
                print("[中止] 检测到处于冷却时间，今日任务可能已完成。")
                await page.screenshot(path=f"debug_cooldown_loop_{i}.png", full_page=True)
                break

            # 步骤 B: 检查 hCaptcha
            await asyncio.sleep(2)
            hcaptcha_iframe = await page.locator("iframe[src*='hcaptcha.com']").count()

            if hcaptcha_iframe > 0:
                print("[动作] 发现 hCaptcha，准备处理...")
                await page.screenshot(path=f"debug_hcaptcha_before_loop_{i}.png")
                
                # 模型容错与降级处理
                try:
                    if solver and hasattr(solver, 'AgentV'):
                        print("[动作] 尝试使用 AgentV 模型自动勾选...")
                        challenger = solver.AgentV(page=page)
                        await challenger.handle_checkbox()
                        await asyncio.sleep(3)
                        if hasattr(challenger, 'execute'):
                            await challenger.execute()
                    else:
                        raise AttributeError("模块中找不到兼容的 API 方法")
                except Exception as e:
                    print(f"[警告] AI 库处理异常 ({e})。启动原生 Playwright 备用方案（强行点击复选框）...")
                    try:
                        # 定位到验证码的 iframe 并强行点击里面的 checkbox
                        frame = page.frame_locator("iframe[src*='hcaptcha.com']").first
                        checkbox = frame.locator("#checkbox")
                        await checkbox.click()
                        print("[动作] 备用点击指令已下达，等待挑战弹窗加载...")
                        await asyncio.sleep(5)
                    except Exception as fallback_e:
                        print(f"[错误] 备用点击方案也失败了: {fallback_e}")

                await page.screenshot(path=f"debug_hcaptcha_after_click_loop_{i}.png")
            else:
                print("[状态] 未发现 hCaptcha，尝试直接推进。")

            # 步骤 C: 检查并点击绿色 Claim 按钮
            print("[动作] 检查绿色按钮状态...")
            try:
                # 模糊匹配所有的绿色按钮
                claim_button = page.locator("button:has-text('Click here to claim'), button:has-text('Complete the captcha'), .btn-success").first
                
                is_disabled = await claim_button.is_disabled()
                if is_disabled:
                    print("[拦截] 绿色按钮处于不可点击状态！可能是 hCaptcha 被隐藏拦截了。")
                    await page.screenshot(path=f"debug_button_disabled_loop_{i}.png")
                    break
                else:
                    print("[动作] 绿色按钮可点击，尝试点击...")
                    await claim_button.click(timeout=5000)
                    await asyncio.sleep(2)
                    await page.screenshot(path=f"debug_after_claim_click_loop_{i}.png")
            except Exception as e:
                print(f"[错误] 无法定位绿色按钮: {e}")
                await page.screenshot(path=f"debug_claim_error_loop_{i}.png")
                break

            # 步骤 D: 处理可能出现的广告弹窗
            try:
                close_ad_btn = page.locator("button:has-text('X'), .close").first
                await close_ad_btn.click(timeout=3000)
                print("[动作] 已关闭广告弹窗。")
            except Exception:
                pass

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
