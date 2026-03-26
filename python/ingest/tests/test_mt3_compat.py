from __future__ import annotations

import torch


def test_build_cache_position_uses_past_length_and_query_length() -> None:
    from aural_ingest.mt3_compat import _build_cache_position

    hidden_states = torch.zeros((2, 4, 8))
    past_key_value = (torch.zeros((2, 6, 7, 16)),)
    cache_position = _build_cache_position(
        hidden_states,
        past_key_value=past_key_value,
        query_length=3,
    )

    assert cache_position.tolist() == [7, 8, 9]


def test_ensure_mt3_transformers_compat_marks_t5_methods() -> None:
    from aural_ingest.mt3_compat import ensure_mt3_transformers_compat
    from transformers.models.t5.modeling_t5 import (
        T5Block,
        T5LayerCrossAttention,
        T5LayerSelfAttention,
    )

    ensure_mt3_transformers_compat()

    assert getattr(T5Block.forward, "_auralprimer_mt3_compat", False) is True
    assert getattr(T5LayerSelfAttention.forward, "_auralprimer_mt3_compat", False) is True
    assert getattr(T5LayerCrossAttention.forward, "_auralprimer_mt3_compat", False) is True
