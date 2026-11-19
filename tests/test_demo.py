"""Night-review #6: the offline demo must actually evidence reconcile (non-zero)."""
import os
import runpy

DEMO = os.path.join(os.path.dirname(__file__), "..", "examples", "demo.py")


def test_demo_reconcile_shows_exact_sent_and_observed_reply(capsys):
    runpy.run_path(DEMO, run_name="__main__")
    out = capsys.readouterr().out
    assert "sent=1 (exact draft match)" in out
    assert "observed reply=1" in out
