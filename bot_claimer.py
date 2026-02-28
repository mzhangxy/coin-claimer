import os
import asyncio
import re
import json
import urllib.request
from playwright.async_api import async_playwright

TARGET_URL = "https://bot-hosting.net/panel/earn"
# 强制清洗：去除不可见字符
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "").strip()
RAW_PROXIES = os.environ.get("PROXY_SERVER", "").strip()
TWOCAPTCHA_API_KEY = os.environ.get("TWOCAPTCHA_API_KEY", "").strip()

KNOWN_HCAPTCHA_SITEKEY = "21335a07-5b97-4a79-b1e9-b197dc35017a"

def get_proxy_list():
    if not RAW_PROXIES:
        return []
    proxies = RAW_PROXIES.replace('\n', ',').split(',')
    return [p.strip() for p in proxies if p.strip()]

# --- 核心革新：直接使用 2Captcha 最新的 V2 JSON API，彻底终结 ERROR_METHOD_CALL ---
async def solve_captcha_v2(api_key, sitekey, url, captcha_type="hcaptcha"):
    print(f"[调试] 启用 2Captcha V2 引擎 -> 类型: {captcha_type} | Sitekey: {sitekey[:10]}...")
    create_task_url = "https://api.2captcha.com/createTask"
    
    # 根据检测到的类型，组装不同的 V2 任务体
    if captcha_type == "turnstile":
        task_payload = {
            "type": "TurnstileTaskProxyless",
            "websiteURL": url,
            "websiteKey": sitekey
        }
    else:
        task_payload = {
            "type": "HCaptchaTaskProxyless",
            "websiteURL": url,
            "websiteKey": sitekey
        }
        
    payload = {
        "clientKey": api_key,
        "task": task_payload
    }
    
    # 步骤 1：创建 JSON 任务
    try:
        req = urllib.request.Request(
            create_task_url, 
            data=json.dumps(payload).encode('utf-8'), 
            headers={'Content-Type': 'application/json'}
        )
        response = await asyncio.to_thread(urllib.request.urlopen, req, timeout=15)
        res_json = json.loads(response.read().decode('utf-8'))
        
        if res_json.get("errorId") != 0:
            return None, f"V2 创单失败: {res_json}"
            
        task_id = res_json.get("taskId")
        print(f"[状态] V2 任务创建成功, 任务流水号: {task_id}")
    except Exception as e:
        return None, f"V2 提交异常: {str(e)}"
        
    # 步骤 2：轮询获取结果
    print(f"[等待] 正在云端破解，请耐心等待 (约 10-35 秒)...")
    get_result_url = "https://api.2captcha.com/getTaskResult"
    result_payload = {
        "clientKey": api_key,
        "taskId": task_id
    }
    
    for _ in range(30):
        await asyncio.sleep(5)
        try:
            req = urllib.request.Request(
                get_result_url, 
                data=json.dumps(result_payload).encode('utf-8'), 
                headers={'Content-Type': 'application/json'}
            )
            response = await asyncio.to_thread(urllib.request.urlopen, req, timeout=10)
            res_json = json.loads(response.read().decode('utf-8'))
            
            if res_json.get("errorId") != 0:
                return None, f"获取结果失败: {res_json}"
                
            if res_json.get("status") == "ready":
                solution = res_json.get("solution", {})
                # hCaptcha 返回 gRecaptchaResponse，Turnstile 返回 token
                token = solution.get("gRecaptchaResponse") or solution.get("token")
                return token, None
        except Exception:
            pass
            
    return None, "V2 轮询超时 (超过 2.5 分钟)"

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
    except Exception:
        pass

async def safe_dump_html(page, path):
    try:
        html_content = await page.content()
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"[状态] 已成功保存当前页面 HTML 到 {path}")
    except Exception:
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
            await browser.close()
            return
            
        i = 1
        while True:
            print(f"\n--- [流程] 开始第 {i} 次收集循环 ---")
            await asyncio.sleep(4)
            
            try:
                close_ad_btn = page.locator("button:has-text('X'), .close").first
                await close_ad_btn.click(timeout=3000)
            except Exception:
                pass

            print("[动作] 正在检查绿色按钮状态与进度...")
            try:
                claim_btn_locator = page.locator(".btn-success").first
                if await claim_btn_locator.count() > 0:
                    btn_text = await claim_btn_locator.inner_text()
                    if "cooldown" in btn_text.lower() or "cool down" in btn_text.lower():
                        print(f"🎉 [成功] 绿色按钮显示为 '{btn_text}'！")
                        print("[结束] 检测到冷却提示，当日收集配额已满，脚本将正常退出。")
                        await safe_screenshot(page, f"debug_success_cooldown_loop_{i}.png")
                        break
            except Exception:
                pass

            needs_captcha = await page.locator("text='Complete the captcha'").count() > 0 or await page.locator("iframe[src*='hcaptcha.com']").count() > 0

            if needs_captcha:
                print("[动作] 确认页面需要处理验证码 (触发 2Captcha V2 API 流程)...")
                
                if not TWOCAPTCHA_API_KEY:
                    print("[警告] 缺少 TWOCAPTCHA_API_KEY 环境变量，无法启动打码服务。")
                    break
                else:
                    sitekey = KNOWN_HCAPTCHA_SITEKEY
                    captcha_type = "hcaptcha"
                    
                    # 动态探测页面使用的是 hCaptcha 还是可能新换的 Turnstile
                    try:
                        if await page.locator("iframe[src*='hcaptcha.com']").count() > 0:
                            iframe_src = await page.locator("iframe[src*='hcaptcha.com']").first.get_attribute("src")
                            sitekey_match = re.search(r'sitekey=([^&]+)', iframe_src)
                            if sitekey_match:
                                sitekey = sitekey_match.group(1)
                        elif await page.locator(".cf-turnstile").count() > 0:
                            captcha_type = "turnstile"
                            sitekey = await page.locator(".cf-turnstile").first.get_attribute("data-sitekey")
                    except Exception:
                        pass

                    token, error_msg = await solve_captcha_v2(TWOCAPTCHA_API_KEY, sitekey, page.url, captcha_type)
                    
                    if token:
                        print("[状态] 成功获取 Token！正在执行底层 JavaScript 双重注入...")
                        # 同时伪装 hCaptcha 和 Turnstile 的全局对象，确保 100% 覆盖
                        await page.evaluate(f'''
                            const token = "{token}";
                            const inputName = "{'cf-turnstile-response' if captcha_type == 'turnstile' else 'h-captcha-response'}";
                            
                            let textareas = document.querySelectorAll(`[name="${{inputName}}"], [name="g-recaptcha-response"]`);
                            if (textareas.length === 0) {{
                                let ta = document.createElement('textarea');
                                ta.name = inputName;
                                ta.style.display = 'none';
                                document.body.appendChild(ta);
                                textareas = [ta];
                            }}
                            textareas.forEach(el => {{ el.value = token; el.innerHTML = token; }});
                            
                            window.hcaptcha = {{
                                getResponse: function() {{ return token; }},
                                getRespKey: function() {{ return ""; }},
                                execute: function() {{ return Promise.resolve(token); }},
                                render: function() {{ return 0; }},
                                reset: function() {{}}
                            }};
                            
                            window.turnstile = {{
                                getResponse: function() {{ return token; }},
                                render: function() {{ return 0; }},
                                reset: function() {{}}
                            }};
                            
                            const btn = document.querySelector(".btn-success");
                            if(btn) {{ btn.removeAttribute("disabled"); btn.classList.remove("disabled"); }}
                        ''')
                        print("[状态] 深度伪造与 Token 注入完毕。")
                        await asyncio.sleep(2)
                    else:
                        print(f"[错误] 2Captcha 识别失败: {error_msg}")
                        print("🛑 [中止] 本次打码无法通过，按设定停止运行并退出脚本。")
                        break
            else:
                print("[状态] 未发现需要验证码的迹象，尝试直接推进。")

            print("[动作] 尝试点击绿色认领按钮...")
            try:
                claim_button = page.locator("button:has-text('Click here to claim'), button:has-text('Complete the captcha'), .btn-success").first
                await claim_button.click(timeout=5000, force=True)
                await asyncio.sleep(2)
            except Exception as e:
                print(f"[错误] 无法定位或点击绿色按钮: {e}")
                print("🛑 [中止] 点击流程异常，按设定停止运行并退出脚本。")
                await safe_screenshot(page, f"debug_claim_error_loop_{i}.png")
                break

            print("[等待] 正在等待进度条 (预设 20 秒)...")
            await asyncio.sleep(20)

            try:
                ok_button = page.locator("button:has-text('OK')").first
                await ok_button.click(timeout=5000)
                print(f"[成功] 第 {i} 次金币收集闭环完成！准备进入下一轮。")
                i += 1 
            except Exception as e:
                print(f"[警告] 未检测到 Success 的 OK 按钮: {e}")
                print("🛑 [中止] 收集流程未能成功闭环 (可能被拦截或未成功提交)，按设定停止运行并退出脚本。")
                await safe_screenshot(page, f"debug_missing_ok_loop_{i}.png")
                break

            await asyncio.sleep(3)

        print("\n[结束] 流程执行完毕，正在关闭浏览器...")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
