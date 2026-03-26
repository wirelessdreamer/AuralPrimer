from __future__ import annotations

import contextlib
import warnings
from typing import Any


_PATCH_APPLIED = False


@contextlib.contextmanager
def suppress_mt3_runtime_warnings():
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r".*torch\.cuda\.amp\.autocast\(args\.\.\.\) is deprecated.*",
            category=FutureWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message=r".*Instantiating a decoder T5Attention without passing `layer_idx`.*",
            category=UserWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message=r".*At least one mel filterbank has all zero values.*",
            category=UserWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message=r".*The `device` argument is deprecated and will be removed in v5 of Transformers\.*",
            category=FutureWarning,
        )
        yield


def _past_key_length(past_key_value: Any) -> int:
    if past_key_value is None:
        return 0
    try:
        return int(past_key_value[0].shape[2])
    except Exception:
        return 0


def _sequence_length(hidden_states: Any, *, query_length: int | None = None) -> int:
    if query_length is not None:
        return int(query_length)
    shape = getattr(hidden_states, "shape", None)
    if shape is None:
        raise ValueError("hidden_states has no shape; cannot infer cache position")
    if len(shape) < 2:
        raise ValueError(f"hidden_states shape {shape!r} is too small for T5 cache inference")
    return int(shape[-2])


def _build_cache_position(hidden_states: Any, *, past_key_value: Any = None, query_length: int | None = None):
    import torch

    past_length = _past_key_length(past_key_value)
    seq_length = _sequence_length(hidden_states, query_length=query_length)
    device = getattr(hidden_states, "device", None)
    return torch.arange(past_length, past_length + seq_length, device=device)


def ensure_mt3_transformers_compat() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return

    from transformers.models.t5.modeling_t5 import (
        T5Block,
        T5LayerCrossAttention,
        T5LayerSelfAttention,
    )

    if not getattr(T5Block.forward, "_auralprimer_mt3_compat", False):
        original_t5_block_forward = T5Block.forward

        def _patched_t5_block_forward(
            self,
            hidden_states,
            *args,
            past_key_values=None,
            past_key_value=None,
            cache_position=None,
            **kwargs,
        ):
            if past_key_value is None and past_key_values is not None:
                past_key_value = past_key_values
            if cache_position is None:
                cache_position = _build_cache_position(hidden_states, past_key_value=past_key_value)
            kwargs["past_key_value"] = past_key_value
            kwargs["cache_position"] = cache_position
            return original_t5_block_forward(self, hidden_states, *args, **kwargs)

        _patched_t5_block_forward._auralprimer_mt3_compat = True
        T5Block.forward = _patched_t5_block_forward

    if not getattr(T5LayerSelfAttention.forward, "_auralprimer_mt3_compat", False):
        original_self_attention_forward = T5LayerSelfAttention.forward

        def _patched_t5_self_attention_forward(
            self,
            hidden_states,
            *args,
            past_key_value=None,
            cache_position=None,
            **kwargs,
        ):
            if cache_position is None:
                cache_position = _build_cache_position(hidden_states, past_key_value=past_key_value)
            kwargs["past_key_value"] = past_key_value
            kwargs["cache_position"] = cache_position
            return original_self_attention_forward(self, hidden_states, *args, **kwargs)

        _patched_t5_self_attention_forward._auralprimer_mt3_compat = True
        T5LayerSelfAttention.forward = _patched_t5_self_attention_forward

    if not getattr(T5LayerCrossAttention.forward, "_auralprimer_mt3_compat", False):
        original_cross_attention_forward = T5LayerCrossAttention.forward

        def _patched_t5_cross_attention_forward(
            self,
            hidden_states,
            key_value_states,
            *args,
            past_key_value=None,
            query_length=None,
            cache_position=None,
            **kwargs,
        ):
            if cache_position is None:
                cache_position = _build_cache_position(
                    hidden_states,
                    past_key_value=past_key_value,
                    query_length=query_length,
                )
            kwargs["past_key_value"] = past_key_value
            kwargs["query_length"] = query_length
            kwargs["cache_position"] = cache_position
            return original_cross_attention_forward(
                self,
                hidden_states,
                key_value_states,
                *args,
                **kwargs,
            )

        _patched_t5_cross_attention_forward._auralprimer_mt3_compat = True
        T5LayerCrossAttention.forward = _patched_t5_cross_attention_forward

    try:
        import torch
        import mt3_infer.models.yourmt3.model.t5mod_helper as ymt3_helper
        import mt3_infer.models.yourmt3.model.ymt3 as ymt3_module

        if not getattr(ymt3_helper.task_cond_dec_generate, "_auralprimer_mt3_compat", False):

            @torch.no_grad()
            def _patched_task_cond_dec_generate(
                decoder,
                decoder_type,
                embed_tokens,
                lm_head,
                encoder_hidden_states,
                shift_right_fn,
                prefix_ids=None,
                max_length=1024,
                stop_at_eos=True,
                eos_id=1,
                pad_id=0,
                decoder_start_token_id=0,
                debug=False,
            ):
                bsz = int(encoder_hidden_states.shape[0])
                device = encoder_hidden_states.device

                if decoder_type == "t5":
                    dec_input_shape = (bsz, 1)
                elif decoder_type == "multi-t5":
                    dec_input_shape = (bsz, decoder.num_channels, 1)
                else:
                    raise ValueError(f"decoder_type {decoder_type} is not supported.")

                if prefix_ids is not None and prefix_ids.numel() > 0:
                    dec_input_ids = shift_right_fn(prefix_ids)
                    prefix_length = int(prefix_ids.shape[-1])
                else:
                    dec_input_ids = torch.tile(torch.LongTensor([decoder_start_token_id]).to(device), dec_input_shape)
                    prefix_length = 0

                dec_hs = decoder(
                    inputs_embeds=embed_tokens(dec_input_ids),
                    encoder_hidden_states=encoder_hidden_states,
                    use_cache=False,
                    return_dict=False,
                )[0]
                logits = lm_head(dec_hs)
                pred_ids = logits.argmax(-1)
                unfinished_sequences = torch.ones(dec_input_shape, dtype=torch.long, device=device)

                for _ in range(max_length - prefix_length - 1):
                    dec_hs = decoder(
                        inputs_embeds=embed_tokens(pred_ids),
                        encoder_hidden_states=encoder_hidden_states,
                        use_cache=False,
                        return_dict=False,
                    )[0]
                    logits = lm_head(dec_hs)
                    if decoder_type == "t5":
                        next_ids = logits[:, -1:, :].argmax(-1)
                    else:
                        next_ids = logits[:, :, -1:, :].argmax(-1)
                    emitted_ids = next_ids.clone()
                    if eos_id is not None:
                        emitted_ids = emitted_ids * unfinished_sequences + pad_id * (1 - unfinished_sequences)
                    pred_ids = torch.cat((pred_ids, emitted_ids), dim=-1)
                    if eos_id is not None:
                        unfinished_sequences = unfinished_sequences * emitted_ids.ne(eos_id).long()
                        if stop_at_eos and unfinished_sequences.max() == 0:
                            break

                return pred_ids

            _patched_task_cond_dec_generate._auralprimer_mt3_compat = True
            ymt3_helper.task_cond_dec_generate = _patched_task_cond_dec_generate
            ymt3_module.task_cond_dec_generate = _patched_task_cond_dec_generate
    except Exception:
        # The helper is only needed when mt3_infer is installed and its YourMT3 backend is importable.
        pass

    _PATCH_APPLIED = True
