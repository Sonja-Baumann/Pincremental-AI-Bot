"""Opt-in integration check that performs one purchase in a running game."""

from __future__ import annotations

import os
import time

import pytest

from cdp_client import connect_pincremental

_VISIBLE_GAME_STATE = r"""(function() {
  var roots = ['#PointPopup', '#TicketPopup', '#ArcadePopup', '#CompanyPopup',
               '#InvestorPopup', '#WizardPopup'];
  var state = [];
  for (var i = 0; i < roots.length; i++) {
    var panel = document.querySelector(roots[i]);
    if (!panel) { state.push(''); continue; }
    var buttons = panel.querySelectorAll('button');
    var entries = [];
    for (var j = 0; j < buttons.length; j++) {
      entries.push([buttons[j].id, buttons[j].textContent.trim(),
                    !!buttons[j].disabled, buttons[j].className].join('|'));
    }
    var currency = panel.querySelector('.MenuCurrency, .Currency');
    state.push([currency ? currency.textContent.trim() : '', entries.join(';')].join('::'));
  }
  return state.join('\n');
})()"""


@pytest.mark.skipif(
    os.environ.get("PINCREMENTAL_LIVE_TEST") != "1",
    reason="set PINCREMENTAL_LIVE_TEST=1 to spend game currency in the live game",
)
def test_one_live_purchase_changes_visible_game_state() -> None:
    port = int(os.environ.get("PINCREMENTAL_CDP_PORT", "9222"))
    strategy = os.environ.get("PINCREMENTAL_UPGRADE_STRATEGY", "ticket-first")
    client = connect_pincremental(port=port, timeout=5.0, poll_interval=0.2)
    try:
        deadline = time.monotonic() + 15.0
        while not client.install_hook():
            if time.monotonic() >= deadline:
                pytest.fail("Pincremental did not finish loading its game hooks")
            time.sleep(0.2)
        client.dismiss_known_popup()
        before = client.evaluate(_VISIBLE_GAME_STATE)

        purchase = client.buy_available_upgrade(strategy)
        assert purchase is not None, "No enabled in-game purchase was available; no action was made"

        after = before
        deadline = time.monotonic() + 5.0
        while after == before and time.monotonic() < deadline:
            time.sleep(0.2)
            after = client.evaluate(_VISIBLE_GAME_STATE)
        assert after != before, (
            f"Clicked {purchase['store']}:{purchase['upgrade']}, but no visible shop state changed"
        )
    finally:
        client.close(release=False)
