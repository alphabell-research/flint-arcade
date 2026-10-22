"""Runs only inside bubblewrap. No credentials or host home are mounted."""
import json
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright

OUTPUT = Path("/output")
URL = "file:///input/game.html"


def snapshot(page):
    return page.evaluate("""() => {
      if (!window.arcade || typeof window.arcade.restart !== 'function' || typeof window.arcade.snapshot !== 'function')
        throw new Error('Provide window.arcade.restart and window.arcade.snapshot');
      const s=window.arcade.snapshot();
      if (!s || !['ready','playing','won','lost'].includes(s.phase) || typeof s.score !== 'number' || !Number.isFinite(s.score))
        throw new Error('snapshot must include a valid phase and finite numeric score');
      if (s.player && (typeof s.player.x !== 'number' || typeof s.player.y !== 'number'))
        throw new Error('player coordinates must be numbers');
      return JSON.parse(JSON.stringify(s));
    }""")


def rendered(page):
    return page.evaluate("""() => Array.from(document.querySelectorAll('canvas')).some(c => {
      if(c.width<100 || c.height<100) return false;
      const ctx=c.getContext('2d'); if(!ctx) return false;
      const pixels=ctx.getImageData(0,0,c.width,c.height).data;
      const colors=new Set(); let visible=0;
      for(let i=0;i<pixels.length;i+=Math.max(4,4*Math.floor(pixels.length/16000))) {
        if(pixels[i+3]) visible++;
        colors.add(pixels[i]+','+pixels[i+1]+','+pixels[i+2]+','+pixels[i+3]);
      }
      return visible>10 && colors.size>4;
    })""")


def exercise(browser, mobile=False):
    errors, requests = [], []
    context = browser.new_context(viewport={"width": 390 if mobile else 1100, "height": 844 if mobile else 800}, is_mobile=mobile, has_touch=mobile, service_workers="block", accept_downloads=False)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)[:400]))
    page.on("dialog", lambda dialog: dialog.dismiss())
    def route(request):
        if request.request.url == URL:
            request.continue_()
        else:
            requests.append(request.request.url[:100])
            request.abort()
    page.route("**/*", route)
    page.goto(URL, wait_until="load", timeout=10_000)
    page.wait_for_timeout(250)
    if errors:
        raise RuntimeError("Game startup JavaScript errors: " + " | ".join(dict.fromkeys(errors[:4]))[:1000])
    try:
        before = snapshot(page)
    except Exception as exc:
        details = " | ".join(dict.fromkeys(errors[:4]))[:1000]
        raise RuntimeError(("Game JavaScript errors: " + details + "; " if details else "") + str(exc)[:800]) from exc
    nonblank = rendered(page)
    page.evaluate("window.arcade.restart()")
    reset = snapshot(page)
    if mobile:
        buttons = page.locator("button:visible")
        tapped = False
        for i in range(min(buttons.count(), 8)):
            text = buttons.nth(i).inner_text().strip().lower()
            if any(word in text for word in ["start", "play", "begin"]):
                buttons.nth(i).tap(timeout=1000)
                tapped = True
                break
        canvas = page.locator("canvas").first
        if not tapped:
            canvas.tap(timeout=1000)
        box = canvas.bounding_box()
        if box:
            page.touchscreen.tap(box["x"] + box["width"] * .75, box["y"] + box["height"] * .6)
        for i in range(min(buttons.count(), 5)):
            text = buttons.nth(i).inner_text().strip().lower()
            if "restart" not in text and "reset" not in text:
                try:
                    buttons.nth(i).tap(timeout=600)
                except Exception:
                    pass
    else:
        page.keyboard.press("Space")
        page.wait_for_timeout(120)
        if snapshot(page)["phase"] == "ready":
            page.locator("canvas").first.click()
        for key in ("ArrowRight", "ArrowUp", "d", "w"):
            page.keyboard.down(key)
            page.wait_for_timeout(120)
            page.keyboard.up(key)
    page.wait_for_timeout(450)
    after = snapshot(page)
    progressed = after != reset and after["phase"] != "ready"
    page.screenshot(path=str(OUTPUT / ("mobile.png" if mobile else "desktop.png")), full_page=False, timeout=5000)
    page.evaluate("window.arcade.restart()")
    restarted = snapshot(page)
    reset_ok = restarted["phase"] in {"ready", "playing"} and restarted["score"] == 0
    result = {"viewport": "mobile" if mobile else "desktop", "canvas_nonblank": nonblank, "input_progress": progressed, "restart": reset_ok, "snapshots": {"initial": before, "reset": reset, "after_input": after, "restarted": restarted}, "runtime_errors": errors[:8], "blocked_requests": len(requests)}
    context.close()
    return result


def main():
    result = {"accepted": False, "checks": [], "errors": [], "scope": "Static policy, canvas rendering, snapshot contract, restart and brief desktop/mobile input smoke tests. Full gameplay and game balance are not measured."}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=sys.argv[1], headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--renderer-process-limit=2", "--disable-background-networking", "--disable-sync", "--disable-extensions", "--disable-component-update", "--disable-features=WebRtcHideLocalIpsWithMdns", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--js-flags=--max-old-space-size=192"])
            for mobile in (False, True):
                result["checks"].append(exercise(browser, mobile))
            browser.close()
        for check in result["checks"]:
            for key in ("canvas_nonblank", "input_progress", "restart"):
                if not check[key]:
                    result["errors"].append(check["viewport"] + ": " + key + " failed")
            if check["runtime_errors"]:
                result["errors"].extend(check["runtime_errors"])
            if check["blocked_requests"]:
                result["errors"].append("Game attempted a forbidden resource request")
        result["accepted"] = not result["errors"] and len(result["checks"]) == 2
    except Exception as exc:
        result["errors"].append(str(exc)[:800])
    (OUTPUT / "result.json").write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
