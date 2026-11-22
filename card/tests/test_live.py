"""Run with HMD_LIVE=1: these use the real Hermes container and make real model calls."""
import os

import pytest

from card import live_checks

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(os.environ.get("HMD_LIVE") != "1", reason="set HMD_LIVE=1 to run against real Hermes")]


def test_a_made_up_quote_is_refused_by_the_hook_under_real_hermes():
    assert live_checks.control()


def test_hostile_cards_reach_no_tool_but_give():
    assert live_checks.hostile()
