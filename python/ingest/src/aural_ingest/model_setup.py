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
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
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
    # Optional: whether the (separately-licensed) weights are already local.
    # Engines whose CODE ships with the sidecar still need their gated weights
    # downloaded once the user accepts the license, so "package present" is not
    # the same as "ready".
    weights_probe: Callable[[], bool] | None = field(
        default=None, repr=False, compare=False
    )

    def describe(self) -> dict[str, Any]:
        installed = False
        try:
            installed = bool(self.probe())
        except Exception:
            installed = False

        weights_present: bool | None = None
        if self.weights_probe is not None:
            try:
                weights_present = bool(self.weights_probe())
            except Exception:
                weights_present = False

        if not installed:
            next_step = "install_package"
        elif weights_present is False:
            # Code is present but the gated weights are not downloaded yet --
            # the user must accept the license, then fetch them.
            next_step = "accept_license"
        elif self.requires_license_acceptance and weights_present is None:
            # No weights probe available; acceptance can't be verified offline.
            next_step = "accept_license"
        else:
            next_step = "ready"

        payload = {
            k: v for k, v in asdict(self).items() if k not in ("probe", "weights_probe")
        }
        payload["package_installed"] = installed
        payload["weights_present"] = weights_present
        payload["next_step"] = next_step
        return payload


def _spec_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def _hf_cache_roots() -> list[Path]:
    """Fallback hub-cache locations, for when ``huggingface_hub`` can't be
    imported (the lightweight CI env). Deliberately checks *every* variable
    huggingface_hub honours rather than guessing which one wins: this is only
    ever used to answer "are the weights somewhere on disk", so a superset of
    candidate roots is the safe shape.
    """
    def _expand(value: str) -> Path:
        return Path(os.path.expandvars(os.path.expanduser(value)))

    roots: list[Path] = []
    for var in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        value = os.environ.get(var, "").strip()
        if value:
            roots.append(_expand(value))
    home = os.environ.get("HF_HOME", "").strip()
    if home:
        roots.append(_expand(home) / "hub")
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg:
        roots.append(_expand(xdg) / "huggingface" / "hub")
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    return roots


def _hf_file_cached(repo_id: str, filename: str) -> bool:
    """Whether one file of a HuggingFace repo is already in the local cache.

    Purely a lookup -- it must never trigger a download, and must never raise.

    Resolution is delegated to ``huggingface_hub`` itself wherever possible.
    Reimplementing its cache-dir precedence is a trap: it honours HF_HUB_CACHE,
    the legacy HUGGINGFACE_HUB_CACHE, HF_HOME and XDG_CACHE_HOME (expanding ~
    and $VARs in each), so a hand-rolled subset reports "not downloaded" for a
    user who has simply relocated their cache -- stranding them in the setup
    dialog re-downloading weights they already have.

    Checking a *specific* file also avoids the false positive where the cache
    holds only a README or a ``.no_exist`` marker for the repo.
    """
    try:
        try:
            from huggingface_hub import try_to_load_from_cache

            resolved = try_to_load_from_cache(repo_id=repo_id, filename=filename)
            # Returns a str path when cached; a sentinel object for a known-absent
            # file; None when simply not cached.
            return isinstance(resolved, str) and Path(resolved).is_file()
        except ImportError:
            pass

        org, _, name = repo_id.partition("/")
        if not org or not name:
            return False
        for root in _hf_cache_roots():
            snapshots = root / f"models--{org}--{name}" / "snapshots"
            if not snapshots.is_dir():
                continue
            for snapshot in snapshots.iterdir():
                if snapshot.is_dir() and (snapshot / filename).is_file():
                    return True
        return False
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
        # The MIT-licensed engine ships inside the sidecar, so there is nothing
        # to pip-install (and a pip install would be invisible to the frozen
        # sidecar anyway). Only the gated weights are fetched per user.
        install_hint="Bundled with AuralStudio — accept the license to download the weights",
        license_accept_url="https://huggingface.co/MuScriptor/muscriptor-medium",
        docs_url="https://github.com/muscriptor/muscriptor",
        requires_license_acceptance=True,
        probe=lambda: _spec_available("muscriptor"),
        # muscriptor pulls hf://MuScriptor/muscriptor-<size>/model.safetensors
        # (see muscriptor.transcription_model), so that is what "downloaded"
        # means here.
        weights_probe=lambda: _hf_file_cached(
            "MuScriptor/muscriptor-medium", "model.safetensors"
        ),
    ),
)


def model_setup_snapshot() -> dict[str, Any]:
    """Per-external-model setup descriptors + status, for the Studio UI."""
    return {"external_models": [m.describe() for m in EXTERNAL_MODEL_SETUPS]}
