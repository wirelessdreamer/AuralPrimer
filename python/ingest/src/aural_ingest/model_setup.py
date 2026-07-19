"""External-model setup descriptors.

Some optional engines are neither bundled nor installable as a hosted modelpack
zip (that path is ``models/modelManager`` + ``preferredModelPacks``): they are
third-party packages the user installs themselves, and their weights live behind
a **license the user must accept at an external site** (e.g. a gated HuggingFace
model page). This module is the machine-readable registry the AuralStudio
"Model setup" surface renders so it can *direct the user to the right place* to
accept each license, show install steps, and report status.

It is intentionally generic -- ``license_accept_url`` is just a URL, so
"HuggingFace or similar locations" all work the same way. Status probing never
imports the heavy package (only ``importlib.util.find_spec``), so this stays
cheap and import-safe in the frozen sidecar.
"""
from __future__ import annotations

import importlib.util
from dataclasses import asdict, dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ExternalModelSetup:
    """One user-installed, license-gated external engine.

    ``license_accept_url`` is where the user accepts the license (a gated HF
    model page, a dataset agreement, a vendor portal -- any URL). ``probe``
    returns whether the package is importable; acceptance itself can't be
    verified offline, so the UI directs the user to accept then re-checks.
    """

    id: str
    name: str
    maker: str
    summary: str
    license: str
    install_hint: str
    license_accept_url: str | None
    docs_url: str | None
    requires_license_acceptance: bool
    # Not serialized directly -- called by :func:`describe`.
    probe: Callable[[], bool] = field(default=lambda: False, repr=False, compare=False)

    def describe(self) -> dict[str, Any]:
        installed = False
        try:
            installed = bool(self.probe())
        except Exception:
            installed = False
        if not installed:
            next_step = "install_package"
        elif self.requires_license_acceptance:
            # Package present; we can't confirm the per-user license acceptance
            # offline, so surface it as the next action with the accept URL.
            next_step = "accept_license"
        else:
            next_step = "ready"
        payload = {k: v for k, v in asdict(self).items() if k != "probe"}
        payload["package_installed"] = installed
        payload["next_step"] = next_step
        return payload


def _spec_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Registry. Add an entry per user-installed, license-gated external engine.
# --------------------------------------------------------------------------- #
EXTERNAL_MODEL_SETUPS: tuple[ExternalModelSetup, ...] = (
    ExternalModelSetup(
        id="muscriptor",
        name="MuScriptor",
        maker="Kyutai × Mirelo",
        summary=(
            "Whole-mix multi-instrument transcription — transcribes the full "
            "mix into per-instrument notes in one pass."
        ),
        license="MIT (code) / CC-BY-NC-4.0 (weights, gated)",
        install_hint="pip install muscriptor",
        license_accept_url="https://huggingface.co/MuScriptor/muscriptor-medium",
        docs_url="https://github.com/muscriptor/muscriptor",
        requires_license_acceptance=True,
        probe=lambda: _spec_available("muscriptor"),
    ),
)


def model_setup_snapshot() -> dict[str, Any]:
    """Per-external-model setup descriptors + status, for the Studio UI."""
    return {"external_models": [m.describe() for m in EXTERNAL_MODEL_SETUPS]}
