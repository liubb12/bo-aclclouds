def solve_turnstile_box(driver, max_wait_sec=25) -> bool:
    """使用 SeleniumBase 官方标准 API 穿透 Turnstile，不再使用易误触的通用 class"""
    driver.switch_to.default_content()
    start = time.time()
    
    # 优先使用 SB 原生 Turnstile 穿透器
    try:
        driver.uc_gui_click_captcha()
        time.sleep(2)
    except Exception:
        pass

    while time.time() - start < max_wait_sec:
        driver.switch_to.default_content()
        body = driver.get_text("body")
        if "成功" in body or "Success" in body:
            print("  ✅ Turnstile 验证已处于通过状态", flush=True)
            return True

        # 只定位 Cloudflare 专用的 iframe 并点击里面的复选框
        try:
            cf_frames = driver.find_elements(By.XPATH, "//iframe[contains(@src, 'challenges.cloudflare.com') or contains(@id, 'cf-chl-widget')]")
            if cf_frames:
                driver.switch_to.frame(cf_frames[0])
                box = driver.find_element(By.XPATH, "//input[@type='checkbox'] | //span[@id='challenge-stage'] | //body")
                physical_click_trusted(driver, box)
                driver.switch_to.default_content()
                time.sleep(2)
        except Exception:
            driver.switch_to.default_content()

        time.sleep(1.5)
        
    driver.switch_to.default_content()
    return False


def login_freemc(driver):
    print("🔑 访问登录页...", flush=True)
    driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
    time.sleep(5)

    print("⌨️ 输入账号密码...", flush=True)
    safe_fill_input(driver, "//input[@type='text' or @type='email' or @name='username']", FREEMC_USER)
    time.sleep(1)
    safe_fill_input(driver, "//input[@type='password' or @name='password']", FREEMC_PASS)
    time.sleep(1)

    print("🛡️ 处理登录页 Turnstile...", flush=True)
    solve_turnstile_box(driver, max_wait_sec=20)
    time.sleep(2)

    print("🚀 点击 Sign in 提交...", flush=True)
    signin_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Sign in') or @type='submit']")
    if signin_btns:
        physical_click_trusted(driver, signin_btns[0])

    logged_in = False
    for _ in range(25):
        if "/login" not in driver.current_url.lower():
            logged_in = True
            break
        time.sleep(1)

    if not logged_in:
        driver.save_screenshot("login_failed.png")
        raise RuntimeError(f"登录失败，停留在: {driver.current_url}")

    print(f"📍 登录成功，当前 URL: {driver.current_url}", flush=True)
