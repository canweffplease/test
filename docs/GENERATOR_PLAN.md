# Workload generator build plan

The generator sits between two things that know nothing about each other. The
Azure trace is a list of LLM requests. HTSIM wants a list of network flows.
That translation is the piece nobody has written, and it is blocking E2, E3
and E5.

    Azure trace ─┐
                 ├─→ generator ─→ workload.cm  ─→ HTSIM
    model config ┘              └ meta.json    ─→ analysis

## Why it is the critical path

E2 cannot produce a result. 1:1 and 4:1 oversubscription measure identically
because eight flows never saturate the pod uplinks, so the topology variable
does not vary.

E3 measures the wrong thing. With eight flows to eight distinct receivers
there is no contention, so the comparison ranks congestion control by how
much it interferes rather than how well it handles congestion. The DCQCN ECN
threshold sweep confirmed this: no setting makes DCQCN beat doing nothing,
because there is nothing to control.

E5 needs both working first.

## Input data

Azure ships two traces, and they map directly onto the two context-length
distributions our methodology already calls for, so E8 comes nearly free.

| file | use |
|---|---|
| `AzureLLMInferenceTrace_conv.csv` | short conversational traffic |
| `AzureLLMInferenceTrace_code.csv` | longer code/document traffic |

Both from https://github.com/Azure/AzurePublicDataset/tree/master/data

Columns: `TIMESTAMP`, `ContextTokens`, `GeneratedTokens`.

---

## M1. Read the trace

Parse both CSVs into requests with arrival time, prompt tokens, output
tokens. Normalise so the first request is at t=0.

**Done when** you can print the first 100 rows and report, per trace: request
count, duration, mean and p99 prompt length, mean arrival rate.

Make the prompt-length histogram while you are here. It goes in the paper and
it tells you what transfer sizes you are actually dealing with.

*Half an evening.*

---

## M2. Cache size

```python
BYTES_PER_TOKEN = 2 * layers * kv_heads * head_dim * dtype_bytes
cache_bytes     = prompt_tokens * BYTES_PER_TOKEN
```

Keep model configs in one dict, not scattered constants.

| model | layers | kv_heads | head_dim | bytes/token |
|---|---|---|---|---|
| Llama-3-8B | 32 | 8 | 128 | 131,072 |
| Llama-3-70B | 80 | 8 | 128 | 327,680 |
| Llama-2-13B | 40 | 40 | 128 | 819,200 |

**Done when** a 4096-token prompt on Llama-3-70B gives exactly
1,342,177,280 bytes.

*One hour.*

---

## M3. When the flow starts

The cache does not exist until prefill finishes, so this is not the arrival
time.

```python
flow_start      = arrival + prefill_queue_wait + prefill_compute
prefill_compute = alpha * P + beta * P**2
```

Model the prefill pool as N instances each serving one request at a time and
simulate that queue to get the wait. Twenty lines, and it is the difference
between a realistic arrival pattern and every flow starting at once.

Fit alpha and beta against Vidur, or pick values putting a 4K prefill near
200 ms and document the choice.

**Done when** flow start times are realistically spread and you can plot
prefill queue depth over time.

*One evening. Fiddliest part outside the load knob.*

---

## M4. Placement and sharding

Assign each request a prefill instance and a decode instance (round robin to
start, this becomes the placement variable later). Then shard.

```python
shard = cache_bytes // TP
for i in range(TP):
    emit(node(prefill_inst, i), node(decode_inst, i), start_ps, shard)
```

Keep `node()` a function with a numbering parameter, because that is where
conventional vs rail lives.

```python
conventional:  node = server * GPUS_PER_SERVER + gpu
rail:          node = gpu * NUM_SERVERS + server
```

Rail-optimised is a renumbering, not a different topology file. HTSIM maps
hosts to ToRs by `HOST_POD_SWITCH(src) = src / radix_down`, so which host IDs
you use decides which switch they hang off.

**Done when** one request produces TP lines between matched GPU indices and
you can say which node is on which ToR.

*Half an evening.*

---

## M5. Emit and verify

```
Nodes 128
Connections 4096
0->64 id 1 start 0 size 167772160
```

Plus a metadata file keyed by flow id carrying request id, deadline, prompt
length and shard index. The `.cm` format has nowhere to put deadlines and we
need them for deadline attainment.

**Three traps.**

1. `start` is in **picoseconds**. 1 us = 1,000,000.
2. The `Connections` header must equal the line count.
3. No single flow may exceed ~2.1 GB. The size field is a 32-bit int, which
   caps us near a 50K context at TP=8.

**Done when** `./scripts/run.sh roce ft128_1os test <file>.cm` completes and
the flow count comes back right.

Start with ten requests, not ten thousand.

*Half an evening. This is the first working generator.*

---

## M6. The offered load knob

The piece three experiments are waiting on.

```python
capacity_bps     = n_prefill_hosts * link_bps
mean_cache_bytes = mean(prompt_tokens) * BYTES_PER_TOKEN
target_rate      = load * capacity_bps / (8 * mean_cache_bytes)   # req/sec

scale       = observed_rate / target_rate
new_arrival = (arrival - t0) * scale
```

Write the capacity definition into the docs. Any defensible definition works
but it must be stated, because every load number in the paper is relative to
it.

**Done when** `--load 0.3` and `--load 0.9` give measurably different
completion times on the same topology, and 1:1 vs 4:1 finally diverge.

**That divergence is the test that the whole generator worked.**

*One evening plus iteration to find the load range where things bind.*

---

## M7. Validation

Assert inside the generator, not in a separate script, so a bad workload
cannot reach a result.

- emitted bytes/sec matches the target within a few percent
- `Connections` header equals line count
- no flow over 2.1 GB
- node IDs within range for the target topology
- flow count returned by the simulator matches what was emitted

*Two hours.*

---

## Interface

```bash
python3 scripts/gen_workload.py \
    --trace data/AzureLLMInferenceTrace_conv.csv \
    --model llama3-70b --tp 8 --nodes 128 \
    --load 0.7 --numbering conventional \
    --duration 2.0 \
    --out workloads/l70b_conv_load70.cm
```

## Two rules

**Test at every milestone.** A ten-flow file through `run.sh` after M5 catches
format errors that would otherwise surface as a mysterious zero-flow result
in week three.

**Commit generated workloads, not just the generator.** When someone asks in
December why a number looks odd, you want the exact input that produced it.

## Timeline

M1 through M5 is roughly three evenings and gives a working generator at
fixed load. M6 is one more and unblocks E2, E3 and E5. M1 and M2 can be done
in parallel by someone else.
