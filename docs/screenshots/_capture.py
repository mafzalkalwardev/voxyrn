"""Capture operator-console pages into docs/screenshots and stitch walkthrough GIFs."""
import time
from pathlib import Path

from PIL import Image, ImageDraw
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

OUT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8000"
PAGES = [
    ("01-dashboard", "/"),
    ("02-preflight", "/preflight"),
    ("03-settings", "/settings"),
    ("04-contacts", "/contacts"),
    ("05-audio", "/audio"),
    ("06-live-run", "/run"),
    ("07-call-logs", "/logs"),
    ("08-leads", "/leads"),
    ("09-recordings", "/recordings"),
    ("10-connected-calls", "/connected-calls"),
    ("11-carrier-crm", "/carrier-crm"),
    ("12-carrier-profile", "/carrier-crm/+15551001001"),
]


def _hide_contact_numbers(path: Path) -> None:
    """Cover the phone column. The contacts workbook holds real numbers."""
    image = Image.open(path).convert("RGB")
    ImageDraw.Draw(image).rectangle((655, 768, 770, 928), fill=(10, 20, 36))
    image.save(path)


def main() -> None:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1440,900")
    opts.add_argument("--hide-scrollbars")
    opts.add_argument("--force-device-scale-factor=1")
    driver = webdriver.Chrome(options=opts)
    frames = []
    crm_frames = []
    try:
        for name, path in PAGES:
            driver.get(BASE + path)
            driver.implicitly_wait(2)
            driver.execute_script("document.documentElement.setAttribute('data-theme','dark');")
            height = driver.execute_script(
                "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight, 900);"
            )
            driver.set_window_size(1440, min(int(height) + 40, 1600))
            target = OUT / f"{name}.png"
            driver.save_screenshot(str(target))
            if name == "04-contacts":
                _hide_contact_numbers(target)
            image = Image.open(target).convert("RGB")
            frame = image.resize((960, int(image.height * 960 / image.width)), Image.Resampling.LANCZOS)
            frames.append(frame)
            if name.startswith("11") or name.startswith("12"):
                crm_frames.append(frame)
            print(name, target.stat().st_size)
        driver.set_window_size(1440, 900)
        driver.get(BASE + "/carrier-crm/+15551001001")
        driver.execute_script("document.documentElement.setAttribute('data-theme','dark');")
        panels = driver.find_elements("css selector", ".panel")
        tops = [0]
        if len(panels) > 1:
            tops.append(max(0, panels[1].location["y"] - 80))
        if panels:
            tops.append(max(0, panels[-1].location["y"] - 80))
        for index, top in enumerate(tops):
            driver.execute_script("window.scrollTo(0, arguments[0]);", top)
            time.sleep(0.3)
            target = OUT / f"13-crm-section-{index + 1}.png"
            driver.save_screenshot(str(target))
            image = Image.open(target).convert("RGB")
            crm_frames.append(image.resize((960, int(image.height * 960 / image.width)), Image.Resampling.LANCZOS))
            print(target.name, target.stat().st_size)
        frames[0].save(
            OUT / "voxyrn-console-walkthrough.gif",
            save_all=True,
            append_images=frames[1:],
            duration=1600,
            loop=0,
            optimize=True,
        )
        crm_frames[0].save(
            OUT / "voxyrn-crm.gif",
            save_all=True,
            append_images=crm_frames[1:],
            duration=1800,
            loop=0,
            optimize=True,
        )
        print("gifs", (OUT / "voxyrn-console-walkthrough.gif").stat().st_size, (OUT / "voxyrn-crm.gif").stat().st_size)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
