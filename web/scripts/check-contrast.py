#!/usr/bin/env python3
"""Verify the Vice City theme tokens meet the contrast floors.

Every text token is checked at >= 7.0:1 against each surface it can sit on;
interactive chrome (borders, icons, fills, status marks) is checked at >= 3.0:1.
Pairs are modeled after the actual token usages in src/styles.css, src/demo.css
and src/components/optimization.css.

Run: python3 scripts/check-contrast.py  (exit 1 on any failure)
"""
import sys

def _lum(hexcolor):
    h = hexcolor.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

def contrast(fg, bg):
    a, b = _lum(fg), _lum(bg)
    lo, hi = min(a, b), max(a, b)
    return (hi + 0.05) / (lo + 0.05)

def mix(a, b, pct):
    """pct% of hex a blended over hex b."""
    va = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    vb = [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "".join(f"{round((pct / 100) * x + (1 - pct / 100) * y):02x}"
                   for x, y in zip(va, vb))

DARK = {
    "bg": "0c0d1b", "panel": "18182d", "panel-light": "252644",
    "line": "7373a8", "text": "fdfbff", "muted": "b3b8de",
    "lime": "e6ffa3", "lime-dark": "263014",
    "pink": "ffabc0", "pink-soft": "3a1f33",
    "blue": "b3bdff", "amber": "ffa34d", "red": "ffa89b",
    "explainer": "bdb3ef", "button": "f5899b",
    "accent-ink": "1e0031", "accent-hover": "ffc7d4",
    "terminal": "16142a", "terminal-text": "89f5e3",
    "scrim": "0c0d1b", "sel": "38035a",
    "tree-running": "b3bdff", "tree-reused": "e6ffa3",
    "tree-warn": "ffa34d", "tree-fail": "ffa89b",
    "tour-accent": "f5899b",
}
LIGHT = {
    "bg": "fdf6ea", "panel": "ffffff", "panel-light": "f3ead6",
    "line": "8a80a0", "text": "252644", "muted": "48486a",
    "lime": "2d5009", "lime-dark": "edf2dc",
    "pink": "7d1d7d", "pink-soft": "f9ecf3",
    "blue": "3a3c9e", "amber": "74400a", "red": "8f2020",
    "explainer": "5b3c7d", "button": "8a1f6a",
    "accent-ink": "fdfbff", "accent-hover": "741b5a",
    "terminal": "16142a", "terminal-text": "89f5e3",
    "scrim": "0c0d1b", "sel": "ffd9e4",
    "tree-running": "b3bdff", "tree-reused": "e6ffa3",
    "tree-warn": "ffa34d", "tree-fail": "ffa89b",
    "tour-accent": "f5899b",
}

def pairs(theme):
    text = [(fg, bg, 7.0)
            for fg in ("text", "muted", "lime", "pink", "blue", "amber", "red")
            for bg in ("bg", "panel", "panel-light")]
    text += [
        ("muted", "lime-dark", 7.0), ("lime", "lime-dark", 7.0),
        ("text", "lime-dark", 7.0),
        ("muted", "pink-soft", 7.0), ("pink", "pink-soft", 7.0),
        ("text", "pink-soft", 7.0),
        ("explainer", "bg", 7.0), ("explainer", "panel", 7.0),
        ("terminal-text", "terminal", 7.0),
        ("tree-running", "terminal", 7.0), ("tree-reused", "terminal", 7.0),
        ("tree-warn", "terminal", 7.0), ("tree-fail", "terminal", 7.0),
        ("accent-ink", "button", 7.0), ("accent-ink", "accent-hover", 7.0),
        ("text", "sel", 7.0),
        ("blue", mix(theme["blue"], theme["panel"], 14), 7.0),
        ("red", mix(theme["red"], theme["panel"], 10), 7.0),
        ("amber", mix(theme["amber"], theme["panel"], 8), 7.0),
        ("muted", mix(theme["muted"], theme["panel"], 12), 7.0),
    ]
    chrome = [("line", bg, 3.0) for bg in ("bg", "panel", "panel-light")]
    chrome += [
        ("muted", "bg", 3.0), ("muted", "panel", 3.0),
        ("button", "bg", 3.0), ("button", "panel", 3.0),
        ("tour-accent", "scrim", 3.0),
        ("lime", "lime-dark", 3.0), ("pink", "pink-soft", 3.0),
        ("lime", "panel-light", 3.0), ("pink", "panel-light", 3.0),
        ("amber", "panel-light", 3.0), ("red", "panel-light", 3.0),
        ("blue", "panel-light", 3.0),
    ]
    return text, chrome

def audit(name, theme):
    failures = []
    text, chrome = pairs(theme)
    for fg, bg, floor in text:
        bgv = theme.get(bg, bg)
        r = contrast(theme[fg], bgv)
        status = "PASS" if r >= floor else "FAIL"
        if r < floor:
            failures.append((name, "text", fg, bg, round(r, 2), floor))
        print(f"  [{name}] text {fg:14s} on {bg:16s} {r:6.2f} (>= {floor:g}) {status}")
    for fg, bg, floor in chrome:
        r = contrast(theme[fg], theme[bg])
        status = "PASS" if r >= floor else "FAIL"
        if r < floor:
            failures.append((name, "elem", fg, bg, round(r, 2), floor))
        print(f"  [{name}] elem {fg:14s} on {bg:16s} {r:6.2f} (>= {floor:g}) {status}")
    return failures

if __name__ == "__main__":
    failures = audit("dark", DARK) + audit("light", LIGHT)
    print(f"\n{len(failures)} failure(s)")
    for f in failures:
        print("  ", f)
    sys.exit(1 if failures else 0)
