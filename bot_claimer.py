import os
import asyncio
import re
from playwright.async_api import async_playwright

# 导入 NopeCHA 官方库
try:
    import nopecha
except ImportError:
    nopecha = None

# 从 GitHub Secrets 获取环境变量
TARGET_URL = "https://bot-hosting.net/panel/earn"
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
RAW_PROXIES = os.environ.get("PROXY_SERVER", "")

# 获取 NopeCHA API Key
NOPECHA_API_KEY = os.environ.get("NOPECHA_API_KEY", "")
if nopecha:
    nopecha.api_key = NOPECHA_API_KEY

def get_proxy_list():
    if not RAW_PROXIES:
        return []
    proxies = RAW_PROXIES.replace('\n', ',').split(',')
    return [p.strip() for p in proxies if p.strip()]

async def get_working_proxy(p, proxy_list):
    print(f"[状态] 发现 {len(proxy_list)} 个备选代理，开始快速可用性检测...")
    for proxy in proxy_list:
        print(f"[检测] 正在测试代理: {proxy}")
        try:
            browser = await p.chromium.launch(headless=True, proxy={"server": proxy})
            context = await browser.new_context()
            page = await context.new_page()
            
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

async def safe_screenshot(page, path):
    try:
        await page.screenshot(path=path, timeout=5000)
    except Exception as e:
        print(f"[警告] 截图保存超时或失败 ({path})，跳过截图。")

async def safe_dump_html(page, path):
    try:
        html_content = await page.content()
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"[状态] 已成功保存当前页面 HTML 到 {path}")
    except Exception as e:
        pass

async def inject_token_and_login(context):
    page = await context.new_page()
    
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
        await safe_screenshot(page, "debug_00_inject_token_error.png")
    return page

async def main():
    if not AUTH_TOKEN:
        print("[错误] 未找到 AUTH_TOKEN 环境变量，脚本终止。")
        return

    proxy_list = get_proxy_list()

    async with async_playwright() as p:
        working_proxy = None
        if proxy_list:
            working_proxy = await get_working_proxy(p, proxy_list)
            if not working_proxy:
                print("[中止] 没有可用代理，放弃本次任务。")
                return

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
            await asyncio.sleep(8) 
        except Exception as e:
            print(f"[致命错误] 访问收集页面超时: {e}")
            await safe_screenshot(page, "debug_01_timeout_error.png")
            await safe_dump_html(page, "debug_01_timeout_error.html")
            await browser.close()
            return
            
        await safe_screenshot(page, "debug_01_after_login.png")

        for i in range(1, 11):
            print(f"\n--- [流程] 开始第 {i}/10 次收集循环 ---")

            cooldown_count = await page.locator("text=You are on cooldown!").count()
            if cooldown_count > 0:
                print("[中止] 检测到处于冷却时间，今日任务可能已完成。")
                await safe_screenshot(page, f"debug_cooldown_loop_{i}.png")
                break

            await asyncio.sleep(4)
            hcaptcha_iframe = await page.locator("iframe[src*='hcaptcha.com']").count()

            if hcaptcha_iframe > 0:
                print("[动作] 发现 hCaptcha，准备提取核心参数并调用 NopeCHA API...")
                await safe_screenshot(page, f"debug_hcaptcha_before_loop_{i}.png")
                
                try:
                    if not nopecha or not NOPECHA_API_KEY:
                        print("[警告] 缺少 nopecha 库或 NOPECHA_API_KEY 环境变量，无法启动打码服务。")
                    else:
                        print("[动作] 正在从页面提取 sitekey...")
                        # 解析 iframe src 以获取 sitekey
                        iframe_src = await page.locator("iframe[src*='hcaptcha.com']").first.get_attribute("src")
                        sitekey_match = re.search(r'sitekey=([^&]+)', iframe_src)
                        
                        if sitekey_match:
                            sitekey = sitekey_match.group(1)
                            print(f"[状态] 成功提取 sitekey: {sitekey}")
                            print(f"[等待] 正在向 NopeCHA 云端发送识别请求... (通常需要 5-20 秒)")
                            
                            # nopecha 的 solve 方法是同步的，使用 asyncio.to_thread 放入线程池运行，防止阻塞异步框架
                            token = await asyncio.to_thread(
                                nopecha.Token.solve,
                                type='hcaptcha',
                                sitekey=sitekey,
                                url=page.url
                            )
                            
                            if token:
                                print("[状态] 成功获取 Token！正在隐形注入页面文本域...")
                                # 通过 JavaScript 将 Token 填入隐藏的验证码表单中
                                await page.evaluate(f'''
                                    const token = "{token}";
                                    const textareas = document.querySelectorAll('[name="h-captcha-response"], [name="g-recaptcha-response"]');
                                    textareas.forEach(el => {{
                                        el.value = token;
                                        el.innerHTML = token;
                                    }});
                                ''')
                                print("[状态] Token 注入完毕。")
                                await asyncio.sleep(2)
                            else:
                                print("[错误] NopeCHA 未返回有效 Token。")
                        else:
                            print("[错误] 正则解析失败，无法在当前页面提取到 sitekey。")
                except Exception as e:
                    print(f"[错误] NopeCHA API 执行异常: {e}")

                await safe_screenshot(page, f"debug_hcaptcha_after_api_loop_{i}.png")
            else:
                print("[状态] 未发现 hCaptcha，尝试直接推进。")

            print("[动作] 检查绿色按钮状态...")
            try:
                # 即使按钮视觉上看起来 is_disabled，底层注入后有时可以直接强制提交
                claim_button = page.locator("button:has-text('Click here to claim'), button:has-text('Complete the captcha'), .btn-success").first
                
                # 去除前端的 disabled 属性限制（防备由于没有点击图片导致的按钮未激活状态）
                await page.evaluate('''() => {
                    const btn = document.querySelector(".btn-success");
                    if(btn) { btn.removeAttribute("disabled"); btn.classList.remove("disabled"); }
                }''')
                
                print("[动作] 尝试点击认领按钮...")
                await claim_button.click(timeout=5000, force=True)
                await asyncio.sleep(2)
                await safe_screenshot(page, f"debug_after_claim_click_loop_{i}.png")
            except Exception as e:
                print(f"[错误] 无法定位或点击绿色按钮: {e}")
                await safe_screenshot(page, f"debug_claim_error_loop_{i}.png")
                break

            try:
                close_ad_btn = page.locator("button:has-text('X'), .close").first
                await close_ad_btn.click(timeout=3000)
                print("[动作] 已关闭广告弹窗。")
            except Exception:
                pass

            print("[等待] 正在等待进度条 (预设 20 秒)...")
            await asyncio.sleep(20)
            await safe_screenshot(page, f"debug_after_progressbar_loop_{i}.png")

            try:
                ok_button = page.locator("button:has-text('OK')").first
                await ok_button.click(timeout=5000)
                print(f"[成功] 第 {i} 次金币收集闭环完成！")
            except Exception as e:
                print(f"[警告] 未检测到 Success 的 OK 按钮: {e}")
                await safe_screenshot(page, f"debug_missing_ok_loop_{i}.png")

            await asyncio.sleep(3)

        print("\n[结束] 脚本执行完毕，准备关闭浏览器。")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
