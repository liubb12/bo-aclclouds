# 1. 打开登录页面
        print(f"🌐 正在打开登录页面: {LOGIN_URL} ...", flush=True)
        driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=5)
        time.sleep(5)
        dismiss_pwa_popups(driver)

        user_selector = "input[type='email'], input[name='email'], input[name='username'], input[type='text']"
        driver.wait_for_element_visible(user_selector, timeout=25)

        user_elem = driver.find_element(By.CSS_SELECTOR, user_selector)
        # 模拟真实点击与按键输入
        user_elem.click()
        time.sleep(0.3)
        user_elem.send_keys(Keys.CONTROL, "a")
        user_elem.send_keys(Keys.BACKSPACE)
        user_elem.send_keys(VOER_USERNAME)
        print(f"  📝 已填入账号: {VOER_USERNAME[:3]}***", flush=True)
        time.sleep(1)

        pwd_elem = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
        pwd_elem.click()
        time.sleep(0.3)
        pwd_elem.send_keys(Keys.CONTROL, "a")
        pwd_elem.send_keys(Keys.BACKSPACE)
        pwd_elem.send_keys(VOER_PASSWORD)
        print("  📝 已填入密码", flush=True)
        time.sleep(1.5)

        # 2. 处理 Cloudflare Turnstile 验证码
        solve_cf_turnstile(driver)
        time.sleep(2)

        print("🔑 正在点击 [Sign in] 提交登录...", flush=True)
        submit_btn = driver.find_element(By.XPATH, "//button[@type='submit' or contains(., 'Sign in') or contains(., 'Login')]")
        try:
            submit_btn.click()
        except Exception:
            driver.execute_script("arguments[0].click();", submit_btn)

        for _ in range(15):
            if "/login" not in driver.current_url.lower():
                break
            time.sleep(1)
