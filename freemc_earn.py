def login_freemc(driver):
    """登录流程：精准填入凭证 -> 物理穿透 Turnstile 等待绿勾 -> 提交"""
    print("🔑 打开登录页...", flush=True)
    driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
    time.sleep(5)

    # 1. 精准定位并输入用户名
    print("⌨️ 输入用户名/邮箱...", flush=True)
    user_input = driver.wait_for_element_visible("input[type='text'], input[type='email']", timeout=15)
    user_input.clear()
    user_input.send_keys(FREEMC_USER)
    time.sleep(1)

    # 2. 精准定位并输入密码（双重保障：send_keys + JS value 确保不为空）
    print("⌨️ 输入密码...", flush=True)
    pwd_input = driver.wait_for_element_visible("input[type='password']", timeout=15)
    pwd_input.clear()
    pwd_input.send_keys(FREEMC_PASS)
    driver.execute_script("arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", pwd_input)
    time.sleep(1)

    # 3. 穿透 Turnstile 并死等它变成“成功/绿勾”
    print("🛡️ 点击 Turnstile 验证框并等待通过...", flush=True)
    solve_turnstile_box(driver, max_wait_sec=20)

    # 等待 Turnstile 状态真正变为通过（最长等待 25 秒）
    turnstile_passed = False
    for sec in range(25):
        body_text = driver.get_text("body")
        if "成功" in body_text or "Success" in body_text:
            turnstile_passed = True
            print(f"  ✅ Turnstile 验证在第 {sec + 1} 秒完成！", flush=True)
            break
        # 如果还没通过，再次尝试物理点击复选框
        cf_xpaths = [
            "//input[@type='checkbox']",
            "//label[contains(@class, 'ctp-checkbox-label')]",
            "//span[contains(@class, 'mark')]",
            "//div[@class='cb-c']"
        ]
        recursive_find_and_click(driver, cf_xpaths, current_depth=0, max_depth=3)
        time.sleep(1.5)

    time.sleep(2)

    # 4. 点击 Sign in
    print("🚀 点击 Sign in 提交登录...", flush=True)
    signin_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Sign in') or @type='submit']")
    if signin_btns:
        physical_click_trusted(driver, signin_btns[0])

    # 5. 等待跳转离开 /login
    logged_in = False
    for _ in range(25):
        if "/login" not in driver.current_url.lower():
            logged_in = True
            break
        time.sleep(1)

    if not logged_in:
        driver.save_screenshot("login_failed.png")
        raise RuntimeError(f"登录失败，仍然停留在: {driver.current_url}")

    print(f"📍 登录成功，当前 URL: {driver.current_url}", flush=True)
