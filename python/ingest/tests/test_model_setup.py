"""Tests for the external-model setup registry that drives AuralStudio's
license-acceptance / setup surface."""
from __future__ import annotations

from aural_ingest import model_setup
from aural_ingest.model_setup import ExternalModelSetup, model_setup_snapshot


def _mk(probe, *, requires=True, weights_probe=None) -> ExternalModelSetup:
    return ExternalModelSetup(
        id="x",
        name="X",
        maker="M",
        summary="s",
        license="MIT / CC-BY-NC-4.0 (gated)",
        install_hint="pip install x",
        license_accept_url="https://example.com/accept",
        docs_url="https://example.com/docs",
        requires_license_acceptance=requires,
        probe=probe,
        weights_probe=weights_probe,
    )


def test_describe_next_step_install_when_absent() -> None:
    d = _mk(lambda: False).describe()
    assert d["package_installed"] is False
    assert d["next_step"] == "install_package"
    assert d["license_accept_url"] == "https://example.com/accept"
    assert "probe" not in d  # callable not serialized


def test_describe_next_step_accept_license_when_installed_and_gated() -> None:
    d = _mk(lambda: True, requires=True).describe()
    assert d["package_installed"] is True
    assert d["next_step"] == "accept_license"


def test_describe_next_step_ready_when_installed_and_not_gated() -> None:
    d = _mk(lambda: True, requires=False).describe()
    assert d["next_step"] == "ready"


def test_describe_probe_exception_is_treated_as_not_installed() -> None:
    def _boom() -> bool:
        raise RuntimeError("nope")

    d = _mk(_boom).describe()
    assert d["package_installed"] is False
    assert d["next_step"] == "install_package"


def test_describe_accept_license_when_code_present_but_weights_missing() -> None:
    # The bundled-engine case: MIT code ships with the sidecar, but the gated
    # weights still have to be downloaded after the user accepts the license.
    d = _mk(lambda: True, weights_probe=lambda: False).describe()
    assert d["package_installed"] is True
    assert d["weights_present"] is False
    assert d["next_step"] == "accept_license"


def test_describe_ready_when_code_and_weights_present() -> None:
    d = _mk(lambda: True, weights_probe=lambda: True).describe()
    assert d["weights_present"] is True
    assert d["next_step"] == "ready"


def test_describe_weights_probe_exception_counts_as_missing() -> None:
    def _boom() -> bool:
        raise RuntimeError("cache unreadable")

    d = _mk(lambda: True, weights_probe=_boom).describe()
    assert d["weights_present"] is False
    assert d["next_step"] == "accept_license"


def test_snapshot_has_muscriptor_with_hf_accept_url() -> None:
    snap = model_setup_snapshot()
    models = {m["id"]: m for m in snap["external_models"]}
    assert "muscriptor" in models
    ms = models["muscriptor"]
    assert ms["license_accept_url"] == "https://huggingface.co/MuScriptor/muscriptor-medium"
    assert ms["requires_license_acceptance"] is True
    assert "CC-BY-NC-4.0" in ms["license"]
    # The engine now ships with the sidecar, so the remaining step is the
    # gated weights -- never "pip install" (invisible to a frozen sidecar).
    assert "pip install" not in ms["install_hint"]
    assert ms["package_installed"] is True
    assert ms["next_step"] in ("accept_license", "ready")


def test_snapshot_is_json_safe() -> None:
    import json

    json.dumps(model_setup_snapshot())  # must not raise (no callables leak)


def test_registry_entries_have_required_fields() -> None:
    for m in model_setup.EXTERNAL_MODEL_SETUPS:
        d = m.describe()
        for key in ("id", "name", "maker", "summary", "license", "install_hint", "next_step"):
            assert d.get(key), f"{m.id} missing {key}"
