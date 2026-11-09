# Workloads

Two kinds live here.

**Hand written**, tracked in git. These predate the generator and are kept
because the early results in `docs/EXPERIMENTS.md` were measured on them.

| file | what it is |
|---|---|
| `kv_llama70b_4k_tp8.cm` | one 4K Llama-3-70B cache, TP=8, 16 nodes |
| `kv_incast_8to1.cm` | eight senders into one receiver |
| `kv128_conv.cm` | the same single transfer on 128 nodes |
| `kv128_rail.cm` | ditto, rail numbering, predates `placement.py` |

**Generated**, not tracked. `gen_workload.py` is deterministic, so a `.cm`
and its `.json` sidecar are reproduced exactly by the command below plus the
Azure trace. Committing them would add megabytes of derived data to every
clone, and the sidecars are the bulk of it.

Regenerate any of these from the repo root. The traces download into `data/`
per `docs/GENERATOR_PLAN.md`.

```bash
# E3, congestion control comparison at load 0.3
for N in conventional rail; do
  python3 scripts/gen_workload.py --trace data/AzureLLMInferenceTrace_conv.csv \
      --model llama2-13b --prefill 8 --decode 8 \
      --load 0.3 --window 0.15 --numbering $N \
      --out workloads/e3_load03_$N
done

# load sweep, 2 second window. Each of these is hours of simulation.
for L in 0.3 0.5 0.7; do
  python3 scripts/gen_workload.py --trace data/AzureLLMInferenceTrace_conv.csv \
      --model llama2-13b --prefill 8 --decode 8 \
      --load $L --window 2.0 --out workloads/conv_13b_load${L/./}
done

# smoke test, finishes in about 20 seconds
python3 scripts/gen_workload.py --trace data/AzureLLMInferenceTrace_conv.csv \
    --model llama2-13b --prefill 8 --decode 8 --rate-scale 5 \
    --window 0.02 --out workloads/smoke_conv_13b
```

Every generated `.cm` has a `.json` beside it recording the full
configuration, so a result can always be traced back to the exact settings
that produced it. If you are reporting a number, cite the sidecar's
`config` block rather than the command you think you ran.
