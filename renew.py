import difflib

def solve_acl_custom_captcha(driver, context_name="登录页"):
    """
    自研验证码全流程处理：
    基于相似度算法容忍 OCR 识别噪点与漏字
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
        prompt_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Click on') or contains(text(), 'click on')]")
        if prompt_elements and any(el.is_displayed() for el in prompt_elements):
            target_element = next(el for el in prompt_elements if el.is_displayed())
            prompt_text = target_element.text.strip()
            print(f"  🧩 发现二次验证点选题: {prompt_text}", flush=True)

            match = re.search(r'[Cc]lick on\s+([A-Za-z0-9_-]+)', prompt_text)
            if match:
                target_word = match.group(1).strip().lower()
                print(f"  🎯 目标关键字为: [{target_word}]", flush=True)

                candidate_cards = driver.execute_script("""
                    const promptEl = arguments[0];
                    let parent = promptEl.parentElement;
                    while (parent && parent.querySelectorAll('canvas, img, div[style*="background"]').length < 4 && parent !== document.body) {
                        parent = parent.parentElement;
                    }
                    if (!parent) return [];

                    let items = Array.from(parent.querySelectorAll('canvas, img'));
                    if (items.length >= 4) return items.slice(0, 4);

                    let divs = Array.from(parent.querySelectorAll('div')).filter(d => {
                        return d.clientHeight > 20 && d.clientWidth > 40 && d.clientHeight < 150 && d.clientWidth < 300;
                    });
                    if (divs.length >= 4) return divs.slice(0, 4);

                    return [];
                """, target_element)

                if not candidate_cards or len(candidate_cards) < 4:
                    candidate_cards = driver.find_elements(By.XPATH, "//*[contains(text(), 'Click on')]/following::canvas | //*[contains(text(), 'Click on')]/following::img")
                    if len(candidate_cards) > 4:
                        candidate_cards = candidate_cards[:4]

                print(f"  🔍 成功定位到 {len(candidate_cards)} 个候选卡片，开始 OCR 识别与相似度比对...", flush=True)

                best_card = None
                best_score = 0.0
                best_text = ""

                for idx, card in enumerate(candidate_cards):
                    card_ocr_text = ""
                    if OCR_AVAILABLE:
                        try:
                            card_png = card.screenshot_as_png
                            card_ocr_text = ocr_recognize_image(card_png).lower()
                        except Exception as ocr_err:
                            print(f"  ⚠️ 卡片 #{idx+1} 识别出错: {ocr_err}")

                    # 计算与目标词的字符相似度 (0.0 ~ 1.0)
                    score = difflib.SequenceMatcher(None, target_word, card_ocr_text).ratio()
                    print(f"  🔍 卡片 #{idx+1} 识别: [{card_ocr_text}] | 相似度得分: {score:.2f}", flush=True)

                    if score > best_score:
                        best_score = score
                        best_card = card
                        best_text = card_ocr_text

                # 相似度达到 0.45 以上即可认为是最佳目标（轻松容忍 1~2 个字母偏差）
                if best_card and best_score >= 0.45:
                    print(f"  ✨ 最佳匹配卡片确认: [{best_text}] (得分: {best_score:.2f})", flush=True)
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", best_card)
                    time.sleep(0.5)
                    try:
                        best_card.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", best_card)
                    print(f"  ✅ 已精准点击目标卡片: {best_text}", flush=True)
                    time.sleep(3)
                else:
                    print(f"  ⚠️ 最高相似度仅为 {best_score:.2f}，低于阈值，跳过点击以防误触", flush=True)
    except Exception as e:
        print(f"  ℹ️ 点选验证阶段处理结束: {e}", flush=True)

    # 3. 验证是否变成 Verified
    for _ in range(6):
        body_str = driver.get_text("body")
        if "Verified" in body_str:
            print("  🟢 验证码已成功变为 Verified 状态！", flush=True)
            return True
        time.sleep(1)
    return False
