"""Every held item in the scraped item pool must be classified somewhere.

When a new regulation adds items, this is the test that fails: each new
item needs a decision — damage effect (damage/items.py IMPLEMENTED), no
damage-roll effect (NO_DAMAGE_EFFECT; model it in sim/ if it changes
battle flow), or a conscious "not modeled yet" entry below.
"""

import pytest

from pokechamp.damage.items import is_implemented
from pokechamp.data.loader import DATA_DIR, load_ruleset

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "champions" / "items.json").exists(),
    reason="champions ruleset not scraped yet",
)

# held items knowingly left unmodeled (the calculator notes them). Empty
# since 2026-10: the last ten (Bright Powder ... White Herb) were modeled.
KNOWN_UNMODELED: set[str] = set()


def test_every_held_item_is_classified():
    rs = load_ruleset("champions")
    unclassified = sorted(
        name for name, item in rs.items.items()
        if item["held"]
        and not item.get("mega_stone")
        and not is_implemented(name)
        and name not in KNOWN_UNMODELED
    )
    assert not unclassified, (
        f"new held items need a decision (see module docstring): {unclassified}"
    )


def test_allowlist_has_no_stale_entries():
    """An item that got modeled (or left the pool) should leave the list."""
    rs = load_ruleset("champions")
    stale = sorted(
        n for n in KNOWN_UNMODELED if n not in rs.items or is_implemented(n)
    )
    assert not stale, stale
