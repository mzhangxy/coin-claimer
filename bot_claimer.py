import os
import asyncio
from playwright.async_api import async_playwright
from twocaptcha import TwoCaptcha

TARGET_URL = "https://bot-hosting.net/panel/earn"

# ====================== 环境变量 ======================
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "").strip()
RAW_PROXIES = os.environ.get("PROXY_SERVER", "").strip()
TWOCAPTCHA_API_KEY = os.environ.get("TWOCAPTCHA_API_KEY", "").strip()

KNOWN_HCAPTCHA_SITEKEY = "21335a07-5b97-4a79-b1e9-b197dc35017a"
MAX_LOOPS = 40  # 防止无限循环，可自行调整

def get_proxy_list():
    if not RAW_PROXIES:
        return []
    proxies = RAW_PROXIES.replace('\n', ',').split(',')
    return [p.strip() for p in proxies if p.strip()]

async def solve_hcaptcha(page_url: str, sitekey: str, api_key: str, proxy: str = None):
    """官方 2Captcha 库 - 最稳定打码函数"""
    solver = TwoCaptcha(api_key)
    try:
        print(f"[2Captcha] 提交 hCaptcha 任务 → {page_url}")
        
        params = {
            "sitekey": sitekey,
            "url": page_url,
            "invisible": 0,
        }
        
        if proxy:
            print(f"[2Captcha] 使用代理打码: {proxy}")
            result = await asyncio.to_thread(
                solver.hcaptcha,
                **params,
                proxy={"type": "http", "uri": proxy}
            )
        else:
            result = await asyncio.to_thread(solver.hcaptcha, **params)
        
        token = result['code']
        print(f"[2Captcha] ✅ Token 获取成功 (长度: {len(token)})")
        return token
    except Exception as e:
        print(f"[2Captcha] ❌ 打码失败: {e}")
        raise

async def get_working_proxy(p, proxy_list):
    """代理可用性测试（已彻底修复崩溃问题）"""
    print(f"[Proxy] 开始测试 {len(proxy_list)} 个代理...")
    for proxy in proxy_list:
        browser = None
        try:
            print(f"[Proxy] 测试: {proxy}")
            browser = await p.chromium.launch(headless=True, proxy={"server": proxy})
            context = await browser.new_context()
            page = await context.new_page()
            
            response = await page.goto("https://bot-hosting.net/", 
                                     timeout=20000, 
                                     wait_until="domcontentloaded")
            
            if response and response.status == 200:
                print(f"[Proxy] ✅ 代理可用: {proxy}")
                await browser.close()
                return proxy
            else:
                print(f"[Proxy] ⚠️ 状态异常: {response.status if response else 'No response'}")
        except Exception as e:
            print(f"[Proxy] ❌ 代理不可用: {e}")
        finally:
            if browser:
                try:
                    await browser.close()
                except:
                    pass
    print("[Proxy] 所有代理不可用 → 使用直连模式")
    return None

async def safe_screenshot(page, filename: str):
    try:
        await page.screenshot(path=filename, timeout=8000)
        print(f"[Debug] 已保存截图: {filename}")
    except:
        pass

async def inject_token(page, token: str, is_turnstile: bool = False):
    """最强 Token 注入 + 主动触发"""
    await page.evaluate(f'''
        const token = "{token}";
        const isTurnstile = {str(is_turnstile).lower()};
        
        // 创建/更新隐藏字段
        const name = isTurnstile ? "cf-turnstile-response" : "h-captcha-response";
        let ta = document.querySelector(`textarea[name="${{name}}"]`);
        if (!ta) {{
            ta = document.createElement('textarea');
            ta.name = name;
            ta.style.display = 'none';
            document.body.appendChild(ta);
        }}
        ta.value = token;
        
        // 模拟全局对象（兼容所有实现）
        if (isTurnstile) {{
            window.turnstile = {{ getResponse: () => token, render: () => 0, reset: () => {{}} }};
        }} else {{
            window.hcaptcha = {{ 
                getResponse: () => token, 
                execute: () => Promise.resolve(token),
                render: () => 0, 
                reset: () => {{}} 
            }};
        }}
        
        // 解除按钮限制并主动点击
        const btns = document.querySelectorAll(".btn-success, button:has-text('Click here'), button:has-text('Claim'), button:has-text('Complete')");
        btns.forEach(btn => {{
            btn.removeAttribute("disabled");
            btn.classList.remove("disabled", "loading");
            btn.click();
        }});
        
        console.log(`[Inject] ✅ Token 已注入并触发 | 类型: ${{isTurnstile ? 'Turnstile' : 'hCaptcha'}}`);
    ''')

async def main():
    if not AUTH_TOKEN:
        print("❌ AUTH_TOKEN 环境变量未设置！")
        return
    if not TWOCAPTCHA_API_KEY:
        print("❌ TWOCAPTCHA_API_KEY 环境变量未设置！")
        return

    proxy_list = get_proxy_list()
    working_proxy = None

    async with async_playwright() as p:
        # 测试代理
        if proxy_list:
            working_proxy = await get_working_proxy(p, proxy_list)

        # 启动浏览器
        launch_args = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-web-security"
            ]
        }
        if working_proxy:
            launch_args["proxy"] = {"server": working_proxy}

        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
        )

        # Stealth 伪装
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        """)

        page = await context.new_page()

        # 注入 Auth Token
        await page.goto("https://bot-hosting.net/", wait_until="domcontentloaded", timeout=60000)
        await page.evaluate(f"window.localStorage.setItem('token', '{AUTH_TOKEN}');")
        print("[Auth] Token 注入完成")

        # 进入目标页面
        print(f"[Main] 跳转目标页面: {TARGET_URL}")
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(6)

        loop_count = 0
        while loop_count < MAX_LOOPS:
            loop_count += 1
            print(f"\n=== 第 {loop_count}/{MAX_LOOPS} 次循环 ===")
            
            await asyncio.sleep(3)

            # 关闭弹窗
            try:
                await page.locator("button:has-text('X'), .close, [aria-label*='Close']").first.click(timeout=3000)
            except:
                pass

            # 检查冷却状态
            try:
                btn_text = await page.locator(".btn-success").first.inner_text(timeout=5000)
                if any(x in btn_text.lower() for x in ["cooldown", "cool down", "wait", "今日"]):
                    print("🎉 当日配额已满，检测到冷却提示，脚本正常退出")
                    await safe_screenshot(page, f"success_cooldown_{loop_count}.png")
                    break
            except:
                pass

            # 检测验证码
            has_captcha = (
                await page.locator("iframe[src*='hcaptcha']").count() > 0 or
                await page.locator(".cf-turnstile").count() > 0 or
                await page.locator("text=Complete the captcha").count() > 0
            )

            if has_captcha:
                print("[Captcha] 检测到验证码，启动解决...")
                try:
                    # 智能提取 sitekey
                    sitekey = await page.evaluate("""() => {
                        const el = document.querySelector('[data-sitekey]');
                        return el ? el.getAttribute('data-sitekey') : null;
                    }""") or KNOWN_HCAPTCHA_SITEKEY
                    
                    is_turnstile = await page.locator(".cf-turnstile").count() > 0
                    
                    token = await solve_hcaptcha(
                        page_url=page.url,
                        sitekey=sitekey,
                        api_key=TWOCAPTCHA_API_KEY,
                        proxy=working_proxy
                    )
                    
                    await inject_token(page, token, is_turnstile)
                    await asyncio.sleep(4)
                except Exception as e:
                    print(f"[Captcha] 打码失败: {e}")
                    await safe_screenshot(page, f"captcha_fail_{loop_count}.png")
                    break

            # 点击 Claim 按钮
            try:
                await page.locator(".btn-success, button:has-text('Click here to claim'), button:has-text('Complete the captcha')").first.click(
                    timeout=8000, force=True
                )
                print("[Action] 已点击 Claim 按钮")
                await asyncio.sleep(3)
            except Exception as e:
                print(f"[Action] 点击按钮失败: {e}")
                await safe_screenshot(page, f"click_fail_{loop_count}.png")
                break

            # 等待成功提示并点击 OK
            try:
                await page.wait_for_selector("button:has-text('OK'), text=Success", timeout=25000)
                await page.locator("button:has-text('OK')").first.click(timeout=5000)
                print(f"✅ 第 {loop_count} 次收集成功")
            except:
                print(f"[Warning] 未检测到 OK 按钮（第 {loop_count} 次）")

            await asyncio.sleep(5)

        print(f"\n[结束] 共执行 {loop_count} 次循环，浏览器关闭中...")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
