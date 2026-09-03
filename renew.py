def solve_acl_custom_captcha(driver, context_name="登录页"):
    """
    自研验证码全流程处理：
    精准提取 4 个选项卡片进行 OCR 并点击
    """
    print(f"  🛡️ 正在处理 [{context_name}] 的 'I am not a robot' 验证码...", flush=True)

    # 1. 物理点击复选框
    clicked = driver.execute_script("""
        const candidates = Array.from(document.querySelectorAll('*')).filter(el => {
            const txt = (el.innerText || el.textContent || '').trim();
            return txt.includes('not a robot') && el.children.length <= 4 && el.clientHeight < 120;
        });

        if (candidates.length === 0) return false;
        
        const container = candidates[0];
        const clickable = container.querySelector('input, span, div, svg') || container;
        clickable.scrollIntoView({ block: 'center' });

        ['mouseover', 'mouseenter', 'mousedown', 'mouseup', 'click'].forEach(evtType => {
            clickable.dispatchEvent(new MouseEvent(evtType, { bubbles: true, cancelable: true, view: window }));
        });
        return true;
    """)

    if clicked:
        print(f"  👉 [{context_name}] 已派发物理事件点击验证码方框，等待响应...", flush=True)
        time.sleep(3)
    else:
        try:
            box_elem = driver.find_element(By.XPATH, "//*[contains(text(), 'not a robot')]/..")
            ActionChains(driver).move_to_element(box_elem).click().perform()
            print(f"  👉 [{context_name}] XPath 点击完成", flush=True)
            time.sleep(3)
        except Exception:
            pass

    # 2. 检测并处理二次点选答题卡
    try:
        # 查找题目元素
        prompt_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Click on') or contains(text(), 'click on')]")
        if prompt_elements and any(el.is_displayed() for el in prompt_elements):
            target_element = next(el for el in prompt_elements if el.is_displayed())
            prompt_text = target_element.text.strip()
            print(f"  🧩 发现二次验证点选题: {prompt_text}", flush=True)

            # 提取目标词 (如 Click on Cloud -> cloud)
            match = re.search(r'[Cc]lick on\s+([A-Za-z0-9_-]+)', prompt_text)
            if match:
                target_word = match.group(1).strip().lower()
                print(f"  🎯 目标关键字为: [{target_word}]", flush=True)

                # 通过 JS 精准抓取题目容器下方的 4 个选项元素
                candidate_cards = driver.execute_script("""
                    const promptEl = arguments[0];
                    // 向上找到包住题目和选项的最近共同容器
                    let parent = promptEl.parentElement;
                    while (parent && parent.querySelectorAll('canvas, img, div[style*="background"]').length < 4 && parent !== document.body) {
                        parent = parent.parentElement;
                    }
                    if (!parent) return [];

                    // 优先抓取 4 个独立的 canvas 或 img
                    let items = Array.from(parent.querySelectorAll('canvas, img'));
                    if (items.length >= 4) return items.slice(0, 4);

                    // 否则抓取包含背景图的子卡片 div
                    let divs = Array.from(parent.querySelectorAll('div')).filter(d => {
                        return d.clientHeight > 20 && d.clientWidth > 40 && d.clientHeight < 150 && d.clientWidth < 300;
                    });
                    if (divs.length >= 4) return divs.slice(0, 4);

                    return [];
                """, target_element)

                # 如果 JS 没抓满，使用 XPath 备选兜底
                if not candidate_cards or len(candidate_cards) < 4:
                    candidate_cards = driver.find_elements(By.XPATH, "//*[contains(text(), 'Click on')]/following::canvas | //*[contains(text(), 'Click on')]/following::img")
                    if len(candidate_cards) > 4:
                        candidate_cards = candidate_cards[:4]

                print(f"  🔍 成功定位到 {len(candidate_cards)} 个候选卡片，开始 OCR 识别...", flush=True)

                matched_card = None
                for idx, card in enumerate(candidate_cards):
                    if not OCR_AVAILABLE:
                        break
                    try:
                        # 对每个独立卡片截图
                        card_png = card.screenshot_as_png
                        ocr_result = ocr_recognize_image(card_png).lower()
                        print(f"  🔍 卡片 #{idx+1} OCR 识别结果: [{ocr_result}]", flush=True)
                        
                        # 字符模糊比对（包含或反向包含，容忍单字符干扰）
                        if target_word in ocr_result or ocr_result in target_word:
                            print(f"  ✨ 卡片 #{idx+1} 成功命中目标 [{target_word}]！", flush=True)
                            matched_card = card
                            break
                    except Exception as ocr_err:
                        print(f"  ⚠️ 卡片 #{idx+1} 识别出错: {ocr_err}")

                if matched_card:
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", matched_card)
                    time.sleep(0.5)
                    try:
                        matched_card.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", matched_card)
                    print(f"  ✅ 已精准点击目标卡片: {target_word}", flush=True)
                    time.sleep(3)
                else:
                    print("  ⚠️ 4 个卡片未完全匹中，尝试点击第一个候选卡片以刷新题目", flush=True)
                    if candidate_cards:
                        driver.execute_script("arguments[0].click();", candidate_cards[0])
                        time.sleep(2)
    except Exception as e:
        print(f"  ℹ️ 点选验证异常或未触发: {e}", flush=True)

    # 3. 验证是否变成 Verified
    for _ in range(6):
        body_str = driver.get_text("body")
        if "Verified" in body_str:
            print("  🟢 验证码已成功变为 Verified 状态！", flush=True)
            return True
        time.sleep(1)
    return False
