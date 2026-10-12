"""Exercise the visible toolbar before invoking its existing source/action buttons."""
def open_synced_original(peer):
    from playwright.sync_api import expect
    trigger=peer.locator('[data-synced-locations]')
    expect(trigger).to_be_enabled(timeout=30_000)
    if trigger.get_attribute('aria-expanded')!='true':
        peer.hover()
        trigger.click()
    button=peer.locator('[data-synced-open]')
    expect(button).to_be_visible()
    button.click()


def open_synced_actions(peer):
    from playwright.sync_api import expect
    trigger=peer.locator('[data-synced-actions-menu]')
    expect(trigger).to_be_enabled(timeout=30_000)
    if trigger.get_attribute('aria-expanded')!='true':
        peer.hover()
        trigger.click()
    expect(peer.locator('[data-synced-actions-panel]')).to_be_visible()
