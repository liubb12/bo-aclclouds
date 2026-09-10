def login_freemc(driver):
    print("🔑 正在登录 Freemchosting...", flush=True)
    driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
    time.sleep(4)

    # 1. 先填入账号和密码
    print("⌨️ 正在输入账号密码...", flush=True)
    driver.type("input[type='text'], input[type='email'], input[name='username']", FREEMC_USER)
    time.sleep(1)
    driver.type("input[type='password'], input[name='password']", FREEMC_PASS)
    time.sleep(1)

    # 2. 尝试穿透并点击 Cloudflare Turnstile 验证框
    print("🛡️ 检测并触发 Turnstile 验证...", flush=True)
    try:
        driver.uc_gui_click_captcha()
    except Exception:
        pass

    # 3. 轮询等待 Turnstile 验证完成（最长等待 20 秒）
    for sec in range(20):
        body_text = driver.get_text("body")
        if "成功" in body_text or "Success" in body_text:
            print(f"  ✅ Turnstile 验证在第 {sec + 1} 秒完成！", flush=True)
            break
        time.sleep(1)

    # 4. 提交登录
    print("🚀 点击 Sign in 提交...", flush=True)
    signin_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Sign in') or @type='submit']")
    if signin_btns:
        physical_click(driver, signin_btns[0])
    time.sleep(8)

    print(f"📍 登录后当前页面: {driver.current_url}", flush=True)
    if "login" in driver.current_url.lower():
        driver.save_screenshot("login_failed.png")
        raise RuntimeError("登录失败：依然停留在登录页，请核对凭证或查看 login_failed.png 截图。")
