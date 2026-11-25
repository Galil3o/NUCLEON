"""Nucleon mail-fetch loop: byte-bounded, folder-wise fetch -> hash -> presence ->
stage (Eingangsordner) -> delete-batch.

Pure and injected: all external effects (IMAP fetch, Drive staging, source deletion,
Drive presence) are passed in as interfaces, so the loop is fully unit-testable with
mocks/synthetic messages. Live wiring (TestAccount IMAP, reserved volume, Proton
Drive) is supplied only in the supervised integration path and is OFF in production
until owner acceptance.
"""
