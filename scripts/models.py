#!/usr/bin/env python3
"""M2. KV cache arithmetic.

    bytes_per_token = 2 * layers * kv_heads * head_dim * dtype_bytes
    cache_bytes     = prompt_tokens * bytes_per_token

The leading 2 is one key tensor plus one value tensor. kv_heads is the number
of key and value heads, which on a model using grouped query attention is
smaller than the number of attention heads. Llama-2-13B has no GQA, so all 40
of its heads carry key and value state, which is why its cache per token is
larger than Llama-3-70B's. That is not a typo and it is worth a sentence in
the paper, because a reader will assume it is one.

Only generated tokens are excluded. The cache transferred at the prefill to
decode handoff covers the prompt, since the decode phase has not produced
anything yet.

Usage
    python3 scripts/models.py
    python3 scripts/models.py --trace data/AzureLLMInferenceTrace_conv.csv
"""

import argparse
import os
import sys

DTYPE_BYTES = {"fp32": 4, "fp16": 2, "bf16": 2, "fp8": 1}

# HTSIM's connection matrix stores a flow size in a 32-bit signed int, so a
# single flow cannot exceed this. Checked here rather than at emit time so a
# bad configuration is caught before a workload is built.
MAX_FLOW_BYTES = 2 ** 31 - 1


class Model:
    def __init__(self, name, layers, kv_heads, head_dim, hidden, params,
                 dtype="fp16"):
        self.name = name
        self.layers = layers
        self.kv_heads = kv_heads
        self.head_dim = head_dim
        self.hidden = hidden          # for the sanity check and the FLOP ratio
        self.params = params          # total parameter count, for prefill cost
        self.dtype = dtype

    @property
    def attn_flop_ratio(self):
        """beta / alpha in the prefill cost model, derived not fitted.

        Prefill FLOPs are roughly

            GEMM      ~ 2 * params * P          (linear in prompt length)
            attention ~ 4 * layers * hidden * P^2

        so the ratio of the quadratic to the linear coefficient is fixed by
        the architecture. DistServe models prefill the same way, a GEMM term
        linear in total tokens plus a FlashAttention term quadratic in
        per-request length. Splitwise measures TTFT growing almost linearly
        with prompt size, which is consistent: this ratio puts attention at
        1.9 percent of Llama-3-70B prefill at 1K tokens and 7.1 percent at 4K.
        """
        return (4.0 * self.layers * self.hidden) / (2.0 * self.params)

    @property
    def bytes_per_token(self):
        return (2 * self.layers * self.kv_heads * self.head_dim
                * DTYPE_BYTES[self.dtype])

    def cache_bytes(self, prompt_tokens):
        return prompt_tokens * self.bytes_per_token

    def shard_bytes(self, prompt_tokens, tp):
        """Bytes held by one tensor parallel rank.

        The cache is sharded over the kv_heads dimension, so tp must divide
        kv_heads. A tp that does not is a configuration error, not something
        to round away, because the rounding would silently change the total
        bytes on the wire.
        """
        if tp < 1:
            raise ValueError("tp must be at least 1, got %d" % tp)
        if self.kv_heads % tp != 0:
            raise ValueError(
                "tp=%d does not divide kv_heads=%d for %s. Valid values are %s."
                % (tp, self.kv_heads, self.name,
                   [d for d in range(1, self.kv_heads + 1)
                    if self.kv_heads % d == 0]))
        total = self.cache_bytes(prompt_tokens)
        assert total % tp == 0
        return total // tp

    def max_tokens_per_flow(self, tp):
        """Longest prompt whose per-rank shard still fits a 32-bit size field."""
        return (MAX_FLOW_BYTES * tp) // self.bytes_per_token

    def check_flow_size(self, prompt_tokens, tp):
        n = self.shard_bytes(prompt_tokens, tp)
        if n > MAX_FLOW_BYTES:
            raise ValueError(
                "a %d token prompt on %s at tp=%d gives a %d byte shard, over "
                "the %d byte limit of HTSIM's 32-bit size field. Max prompt "
                "at this tp is %d tokens."
                % (prompt_tokens, self.name, tp, n, MAX_FLOW_BYTES,
                   self.max_tokens_per_flow(tp)))
        return n


MODELS = {
    # name          layers kv_heads head_dim hidden  params
    "llama3-8b":  Model("llama3-8b",  32,  8, 128, 4096,  8.0e9),
    "llama2-13b": Model("llama2-13b", 40, 40, 128, 5120, 13.0e9),
    "llama3-70b": Model("llama3-70b", 80,  8, 128, 8192, 70.0e9),
}


def self_check():
    """Assertions that would catch a bad edit to the table above."""
    # head_dim must be consistent with hidden size and the attention head
    # count. For GQA models the attention head count is hidden/head_dim, which
    # is a weaker check than for kv_heads but still catches a typo.
    for m in MODELS.values():
        assert m.hidden % m.head_dim == 0, m.name
        assert (m.hidden // m.head_dim) % m.kv_heads == 0, m.name

    # The number the plan names as M2's done condition.
    m = MODELS["llama3-70b"]
    assert m.bytes_per_token == 327680, m.bytes_per_token
    assert m.cache_bytes(4096) == 1342177280, m.cache_bytes(4096)
    assert m.shard_bytes(4096, 8) == 167772160, m.shard_bytes(4096, 8)

    # The shard size the existing hand written workload already uses, so M2
    # agrees with the preliminary results rather than quietly contradicting
    # them.
    assert MODELS["llama3-70b"].shard_bytes(4096, 8) == 167772160

    assert MODELS["llama2-13b"].bytes_per_token == 819200
    assert MODELS["llama3-8b"].bytes_per_token == 131072

    # Sharding must conserve bytes.
    for m in MODELS.values():
        for tp in (1, 2, 4, 8):
            if m.kv_heads % tp == 0:
                assert m.shard_bytes(1000, tp) * tp == m.cache_bytes(1000)


def print_table():
    print("%-12s %6s %8s %8s %12s %12s %12s"
          % ("model", "layers", "kv_heads", "head_dim", "B/token",
             "4K cache", "shard tp=8"))
    for m in MODELS.values():
        try:
            shard = "%.1f MB" % (m.shard_bytes(4096, 8) / 1e6)
        except ValueError:
            shard = "n/a"
        print("%-12s %6d %8d %8d %12d %9.2f GB %12s"
              % (m.name, m.layers, m.kv_heads, m.head_dim, m.bytes_per_token,
                 m.cache_bytes(4096) / 1e9, shard))
    print()
    for m in MODELS.values():
        print("%-12s max prompt at tp=1 %9d tokens, at tp=8 %9d tokens"
              % (m.name, m.max_tokens_per_flow(1),
                 m.max_tokens_per_flow(8) if m.kv_heads % 8 == 0 else -1))


def trace_report(path, tp):
    """What the real traces turn into, in bytes. Ties M1 to M2."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from trace import load_trace, percentile

    reqs, meta = load_trace(path)
    prompts = sorted(r.prompt_tokens for r in reqs)
    n = len(prompts)
    print("\n== %s  (%d requests)" % (meta["name"], n))
    print("%-12s %12s %12s %12s %12s"
          % ("model", "mean cache", "p50", "p99", "max shard tp=%d" % tp))
    for m in MODELS.values():
        mean_b = sum(prompts) / n * m.bytes_per_token
        p50 = m.cache_bytes(percentile(prompts, 50))
        p99 = m.cache_bytes(percentile(prompts, 99))
        over = sum(1 for p in prompts if m.shard_bytes(p, tp) > MAX_FLOW_BYTES)
        print("%-12s %9.1f MB %9.1f MB %9.1f MB %9.1f MB%s"
              % (m.name, mean_b / 1e6, p50 / 1e6, p99 / 1e6,
                 m.shard_bytes(prompts[-1], tp) / 1e6,
                 "" if over == 0 else "   %d requests OVER LIMIT" % over))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trace", action="append", default=[],
                    help="also report cache sizes for this trace")
    ap.add_argument("--tp", type=int, default=8)
    args = ap.parse_args(argv)

    self_check()
    print("self check passed\n")
    print_table()
    for path in args.trace:
        trace_report(path, args.tp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
