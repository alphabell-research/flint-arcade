"""Runs only inside bubblewrap. No credentials or host home are mounted."""
import json
import re
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright

OUTPUT = Path("/output")
URL = "file:///input/game.html"


def browser_error(error):
    message = str(error)[:350]
    stack = getattr(error, "stack", "") or ""
    locations = [line.strip()[:180] for line in stack.splitlines() if "file:///input/game.html:" in line]
    return (message + ("\n" + "\n".join(locations[:3]) if locations else ""))[:750]


def snapshot(page):
    return page.evaluate("""() => {
      if (!window.arcade || typeof window.arcade.restart !== 'function' || typeof window.arcade.snapshot !== 'function')
        throw new Error('Provide window.arcade.restart and window.arcade.snapshot');
      const s=window.arcade.snapshot();
      if (!s || !['ready','playing','won','lost'].includes(s.phase) || typeof s.score !== 'number' || !Number.isFinite(s.score))
        throw new Error('snapshot must include a valid phase and finite numeric score');
      if (s.player && (typeof s.player.x !== 'number' || typeof s.player.y !== 'number' || !Number.isFinite(s.player.x) || !Number.isFinite(s.player.y)))
        throw new Error('player coordinates must be finite numbers');
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


def first_visible_button(page, labels):
    buttons = page.get_by_role("button", name=re.compile(r"\b(?:" + labels + r")\b", re.I))
    for index in range(buttons.count()):
        button = buttons.nth(index)
        if button.is_visible():
            return button
    return None


def visible_button(page, labels):
    return first_visible_button(page, labels) is not None


def state_progress(before, after):
    if before["phase"] != "playing":
        return False
    if before["score"] != after["score"]:
        return True
    if before.get("player") and after.get("player"):
        return any(abs(after["player"][axis] - before["player"][axis]) > .1 for axis in ("x", "y"))
    previous = {key: value for key, value in before.items() if key != "phase"}
    current = {key: value for key, value in after.items() if key != "phase"}
    return previous != current


def held_touch(page, session, target):
    box = target.bounding_box()
    if not box:
        return False
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    size = page.viewport_size
    if x < 0 or y < 0 or x >= size["width"] or y >= size["height"]:
        return False
    session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    try:
        page.wait_for_timeout(240)
    finally:
        session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    return True


def exercise(browser, mobile=False):
    errors, requests = [], []
    context = browser.new_context(viewport={"width": 390 if mobile else 1100, "height": 844 if mobile else 800}, is_mobile=mobile, has_touch=mobile, service_workers="block", accept_downloads=False)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(browser_error(error)))
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
    start_button = visible_button(page, "start|play|begin")
    restart_button = visible_button(page, "restart|reset")
    nonblank = rendered(page)
    page.evaluate("window.arcade.restart()")
    reset = snapshot(page)
    start = first_visible_button(page, "start|play|begin")
    if snapshot(page)["phase"] == "ready" and start is not None:
        try:
            start.tap(timeout=1000) if mobile else start.click(timeout=1000)
        except Exception as exc:
            errors.append("Start control failed: " + str(exc)[:300])
    if snapshot(page)["phase"] == "ready" and not mobile:
        page.keyboard.press("Space")
        page.wait_for_timeout(120)
    if snapshot(page)["phase"] == "ready":
        try:
            target = page.locator("canvas").first
            target.tap(timeout=1000) if mobile else target.click(timeout=1000)
        except Exception as exc:
            errors.append("Canvas start control failed: " + str(exc)[:300])
    started = snapshot(page)
    measurements, touch_axes, control_errors = [], {}, []
    if mobile:
        buttons = page.locator("button:visible")
        groups = {"horizontal": [], "vertical": []}
        for index in range(min(buttons.count(), 16)):
            button = buttons.nth(index)
            label = (button.get_attribute("aria-label") or button.inner_text()).strip().lower()
            if re.search(r"\b(?:left|right)\b|[\u25c0\u25c4\u2190\u25b6\u25ba\u2192]", label):
                groups["horizontal"].append((button, label))
            elif re.search(r"\b(?:up|down|jump)\b|[\u25b2\u25bc\u2191\u2193]", label):
                groups["vertical"].append((button, label))
        session = context.new_cdp_session(page)
        if not any(groups.values()):
            groups = {"canvas": [(page.locator("canvas").first, "canvas touch hold")]}
        for group, targets in groups.items():
            if not targets:
                continue
            touch_axes[group] = False
            for target, label in targets[:3]:
                prior = snapshot(page)
                if prior["phase"] != "playing":
                    break
                applied = held_touch(page, session, target)
                current = snapshot(page)
                progress = applied and state_progress(prior, current)
                measurements.append({"input": label, "group": group, "before": prior, "after": current, "progress": progress})
                touch_axes[group] = touch_axes[group] or progress
                if progress:
                    break
            if not touch_axes[group]:
                control_errors.append(group + " touch controls did not produce movement, score or game-state progress after Start")
    else:
        for key in ("ArrowRight", "ArrowUp", "d", "w"):
            prior = snapshot(page)
            if prior["phase"] != "playing":
                break
            page.keyboard.down(key)
            page.wait_for_timeout(120)
            page.keyboard.up(key)
            current = snapshot(page)
            measurements.append({"input": key, "before": prior, "after": current, "progress": state_progress(prior, current)})
    page.wait_for_timeout(200)
    restart_button = restart_button or visible_button(page, "restart|reset")
    after = snapshot(page)
    nonblank_after_input = rendered(page)
    progressed = any(item["progress"] for item in measurements) and all(touch_axes.values())
    page.screenshot(path=str(OUTPUT / ("mobile.png" if mobile else "desktop.png")), full_page=False, timeout=5000)
    page.evaluate("window.arcade.restart()")
    restarted = snapshot(page)
    reset_ok = restarted["phase"] in {"ready", "playing"} and restarted["score"] == 0
    result = {"viewport": "mobile" if mobile else "desktop", "start_button": start_button, "restart_button": restart_button, "canvas_nonblank": nonblank, "canvas_after_input": nonblank_after_input, "input_progress": progressed, "restart": reset_ok, "input_measurements": measurements, "touch_axes": touch_axes, "control_errors": control_errors, "snapshots": {"initial": before, "reset": reset, "after_start": started, "after_input": after, "restarted": restarted}, "runtime_errors": errors[:8], "blocked_requests": len(requests)}
    context.close()
    return result


def main():
    result = {"accepted": False, "checks": [], "errors": [], "scope": "Static policy, visible Start/Restart controls, canvas rendering, finite snapshot contract, restart and brief desktop/mobile input smoke tests. Full gameplay and game balance are not measured."}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=sys.argv[1], headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--renderer-process-limit=2", "--disable-background-networking", "--disable-sync", "--disable-extensions", "--disable-component-update", "--disable-features=WebRtcHideLocalIpsWithMdns", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--js-flags=--max-old-space-size=192"])
            for mobile in (False, True):
                result["checks"].append(exercise(browser, mobile))
            browser.close()
        for check in result["checks"]:
            for key in ("start_button", "restart_button", "canvas_nonblank", "canvas_after_input", "input_progress", "restart"):
                if not check[key]:
                    result["errors"].append(check["viewport"] + ": " + key + " failed")
            result["errors"].extend(check.get("control_errors", []))
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
