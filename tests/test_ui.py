from playwright.sync_api import sync_playwright
import os
import pathlib

def test_cuj(page):
    page.goto("http://127.0.0.1:8080/")
    page.wait_for_timeout(500)

    # Assert the new UI panel
    panel = page.locator("h2:has-text('Theory of Operation')")
    assert panel.count() > 0, "Theory of Operation heading not found!"

    # Verify the new Question Budget Targets UI
    mean_label = page.locator("label:has-text('Questions per slide')")
    total_label = page.locator("label:has-text('Total questions')")
    assert mean_label.count() > 0, "Questions per slide label not found!"
    assert total_label.count() > 0, "Total questions label not found!"

    # Verify input fields
    target_mean = page.locator("input#target_mean")
    target_total = page.locator("input#target_total")
    assert target_mean.input_value() == "2.5", "Target mean input should default to 2.5!"
    assert target_total.input_value() == "", "Target total input should be blank!"

    # Verify radios are unchecked initially
    mean_radio = page.locator("input[value='mean']")
    total_radio = page.locator("input[value='total']")
    assert not mean_radio.is_checked(), "Mean radio should not be checked by default!"
    assert not total_radio.is_checked(), "Total radio should not be checked by default!"

    # Interact
    mean_radio.click()
    page.wait_for_timeout(500)

    # Take screenshot at the key moment
    os.makedirs("test-outputs", exist_ok=True)
    page.screenshot(path="test-outputs/verification.png", full_page=True)
    page.wait_for_timeout(1000)

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        os.makedirs("test-outputs", exist_ok=True)
        context = browser.new_context(
            record_video_dir="test-outputs"
        )
        page = context.new_page()
        try:
            test_cuj(page)
        finally:
            context.close()
            browser.close()
