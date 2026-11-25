"""Nucleon pipeline: the one reversible loop, composed from the existing stages.

Nucleon has two complementary passes, both reversible and both default-off for anything
destructive:

  1. **Triage** (``nucleon.engine.run_once``): classify -> gate -> label / stage-draft /
     (optional) trash -> journal. This is the reply/label path. The gate is the security
     boundary; only ApplyLabel / StageDraft / DeleteToTrash exist (no send, no hard delete).

  2. **Archive** (``nucleon.pipeline.archive.run_archive``): for each folder,
     cache -> compare (attachment presence on the Drive) -> file (stage absent attachments
     into the Eingangsordner) -> **PDF-Abdruck** -> delete the source. Deletion is the only
     step that removes anything, it is off by default, and it is refused unless a durable
     Abdruck of the source is made first (fail-closed).

The stages are the modules already in the tree; this package only wires the archive pass and
adds the Abdruck stage:

  - classifier: ``nucleon.quarantine`` (quarantined; stub/mock in tests)
  - gate:       ``nucleon.gate``
  - presence:   ``nucleon.presence``
  - filing:     ``nucleon.filing`` (Router + RoutingStager; routes to the flat Eingangsordner)
  - abdruck:    ``nucleon.pipeline.abdruck`` (Chromium print-to-pdf; injected runner)
  - draft:      ``nucleon.actions`` / ``nucleon.models.StageDraft`` (drafts-only, never SMTP)

Default-off contract: ``run_archive(do_delete=False)`` and every optional rule in the filing
Router disabled. Nothing here starts a connection or writes to a real mailbox/Drive on its own.
"""
from nucleon.pipeline.abdruck import Abdruck, ChromiumAbdruck, message_to_html
from nucleon.pipeline.archive import ArchiveReport, run_archive

__all__ = ["Abdruck", "ChromiumAbdruck", "message_to_html", "ArchiveReport", "run_archive"]
