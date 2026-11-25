"""Nucleon filing: where a staged attachment goes on the Drive, and how it gets there.

Two pieces, both pure/injected:
  - ``Router`` decides the target Drive folder. Default policy routes everything to the
    flat Eingangsordner; optional rules can later route selected items into case folders,
    but every rule is OFF by default (nothing auto-lands in CASES at the start).
  - ``RoutingStager`` implements the ``mailfetch.loop.Stager`` protocol: it asks the Router
    for a target and hands the bytes to an injected uploader (mocked in tests; the live
    adapter shells to the Proton Drive CLI with ``-f rename``).
"""
