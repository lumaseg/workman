"""Matching an application's identity across packaging variants.

The same app reports a different identifier depending on how it was installed:
Flatpak Firefox is ``org.mozilla.firefox``, the distro package is plain
``firefox``. A session saved on one machine should still restore on the other,
so matching falls back through progressively looser tiers.

Exact matches always win — looser tiers are consulted only when nothing matches
at a tighter one — so a window carrying the real identifier is never mis-routed
to a loosely-matching one.

This mirrors ``matchWindows()`` in the GNOME Shell extension, which performs
the same three-tier match on the far side of the DBus boundary. The extension's
copy is deliberately left alone: it is GNOME-side code, and changing it would
force existing users to reinstall the extension and log out.
"""

# 0 is an exact match; larger numbers are looser.
EXACT, CASE_INSENSITIVE, LAST_SEGMENT, SUBSTRING = 0, 1, 2, 3


def match_tier(candidate, wanted):
    """How closely ``candidate`` matches ``wanted``; None if not at all."""
    if not candidate or not wanted:
        return None
    if candidate == wanted:
        return EXACT
    lower_candidate, lower_wanted = candidate.lower(), wanted.lower()
    if lower_candidate == lower_wanted:
        return CASE_INSENSITIVE
    # org.mozilla.firefox -> firefox
    if lower_candidate.split(".")[-1] == lower_wanted.split(".")[-1]:
        return LAST_SEGMENT
    if lower_candidate in lower_wanted or lower_wanted in lower_candidate:
        return SUBSTRING
    return None


def best_matches(wanted, candidates, key):
    """Candidates matching ``wanted``, at the tightest tier any of them reaches.

    ``key`` extracts the identifier from a candidate. Order within a tier is
    preserved, so callers can still fall back on enumeration order.
    """
    by_tier = {}
    for candidate in candidates:
        tier = match_tier(key(candidate), wanted)
        if tier is not None:
            by_tier.setdefault(tier, []).append(candidate)
    if not by_tier:
        return []
    return by_tier[min(by_tier)]
