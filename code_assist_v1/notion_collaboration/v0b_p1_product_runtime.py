"""Small, product-only browser helpers used by the V0B capture lane."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .approved_dev_pair import ApprovedDevPairError


V0B_CHROME_EXECUTABLE_ENV = "CODE_ASSIST_V0B_CHROME_EXECUTABLE_PATH"


def v0b_chrome_launch_options() -> dict[str, Any]:
    """Return the deterministic headless Chrome launch configuration."""

    configured = os.environ.get(V0B_CHROME_EXECUTABLE_ENV, "").strip()
    if not configured:
        return {"channel": "chrome", "headless": True}
    try:
        executable = Path(configured).resolve(strict=True)
    except OSError as exc:
        raise ApprovedDevPairError("V0B_CHROME_EXECUTABLE_INVALID") from exc
    if not executable.is_file():
        raise ApprovedDevPairError("V0B_CHROME_EXECUTABLE_INVALID")
    return {"executable_path": str(executable), "headless": True}


ACCESSIBILITY_SCRIPT = r"""
({selectors, liveExpected}) => {
  const implicitRole = element => {
    const tag = String(element?.tagName || '').toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    if (tag === 'main') return 'main';
    if (tag === 'nav') return 'navigation';
    if (tag === 'aside') return 'complementary';
    if (tag === 'a' && element.hasAttribute('href')) return 'link';
    if (tag === 'input') {
      const type = String(element.type || 'text').toLowerCase();
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'search') return 'searchbox';
      return 'textbox';
    }
    return 'generic';
  };
  const name = element => String(
    element?.getAttribute?.('aria-label')
    || element?.getAttribute?.('title')
    || element?.getAttribute?.('placeholder')
    || element?.textContent
    || element?.id
    || 'unnamed'
  ).replace(/\s+/g, ' ').trim().slice(0, 240) || 'unnamed';
  const state = element => {
    if (!element) return 'missing';
    if (element.hidden || getComputedStyle(element).display === 'none' || getComputedStyle(element).visibility === 'hidden') return 'hidden';
    const values = ['visible'];
    if (element === document.activeElement) values.push('focused');
    if (element.disabled) values.push('disabled');
    for (const attribute of ['aria-expanded', 'aria-selected', 'aria-checked', 'aria-pressed', 'aria-current']) {
      if (element.hasAttribute?.(attribute)) values.push(`${attribute.slice(5)}=${element.getAttribute(attribute)}`);
    }
    return values.join(',');
  };
  const describe = (alias, element) => ({
    alias,
    name: name(element),
    role: String(element?.getAttribute?.('role') || implicitRole(element)),
    state: state(element),
  });
  const nodes = Object.entries(selectors).map(([alias, selector]) => {
    const element = document.querySelector(selector);
    return describe(alias, element);
  });
  const active = document.activeElement;
  const activeAlias = Object.entries(selectors).find(([, selector]) => document.querySelector(selector) === active)?.[0] || 'ACTIVE-ELEMENT';
  const live = [...document.querySelectorAll('[aria-live], [role="status"], [role="alert"], [role="log"]')]
    .filter(element => !element.hidden && getComputedStyle(element).display !== 'none' && getComputedStyle(element).visibility !== 'hidden')
    .map((element, index) => ({
      ...describe(`LIVE-${String(index + 1).padStart(2, '0')}`, element),
      live: String(element.getAttribute('aria-live') || (element.getAttribute('role') === 'alert' ? 'assertive' : 'polite')),
    }));
  const scrolling = document.scrollingElement || document.documentElement;
  return {
    status: nodes.every(node => node.state.startsWith('visible')) && (!liveExpected || live.length) ? 'pass' : 'fail',
    focus: {
      active_alias: activeAlias,
      name: name(active),
      role: String(active?.getAttribute?.('role') || implicitRole(active)),
      state: state(active),
    },
    name_role_state: nodes,
    live_regions: {expected: Boolean(liveExpected), nodes: live},
    scroll_geometry: {
      x: 0,
      y: 0,
      width: Math.round(innerWidth),
      height: Math.round(innerHeight),
      scroll_x: Math.round(scrolling.scrollLeft || scrollX || 0),
      scroll_y: Math.round(scrolling.scrollTop || scrollY || 0),
    },
  };
}
"""


__all__ = ["ACCESSIBILITY_SCRIPT", "v0b_chrome_launch_options"]
