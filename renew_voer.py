def wait_and_get_dashboard_info(driver):
    """通过整页纯文本提取倒计时与进度，并保存排查截图"""
    raw_time = "00:00:00"
    ext_prog = "未知"
    total_seconds = 0

    print("⏳ 等待控制台数据动态渲染（最多 25 秒）...")
    for i in range(25):
        try:
            # 直接抓取 body 的可见纯文本，无视复杂的 DOM 嵌套
            body_text = driver.find_element(By.TAG_NAME, "body").text

            # 1. 提取 HH:MM:SS 倒计时（匹配 Time Remaining 附近的或者任意非 00:00:00 的时间）
            matches = re.findall(r"\b(\d{2}):(\d{2}):(\d{2})\b", body_text)
            for m in matches:
                sec = int(m[0]) * 3600 + int(m[1]) * 60 + int(m[2])
                if sec > 0:
                    total_seconds = sec
                    raw_time = f"{m[0]}:{m[1]}:{m[2]}"
                    break

            # 2. 提取 1/4 进度
            prog_match = re.search(r"(\d+/\d+)", body_text)
            if prog_match:
                ext_prog = prog_match.group(1)

            # 只要时间抓到了就直接收工
            if total_seconds > 0:
                print(f"✨ 成功捕获真实数据 (第 {i+1} 秒): {raw_time} | 进度: {ext_prog}")
                break
        except Exception:
            pass

        time.sleep(1)

    # 截图保存，方便排查
    try:
        driver.save_screenshot("debug_dashboard.png")
        print("📸 已保存调试截图: debug_dashboard.png")
    except Exception:
        pass

    return total_seconds, raw_time, ext_prog
