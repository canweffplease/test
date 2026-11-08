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
threshold sweep confirmed this. No setting makes DCQCN beat doing nothing,
because there is nothing to control.

E5 needs both working first.

## Input data

Azure ships two traces, and they map directly onto the two context-length
distributions the methodology already calls for, so E8 comes nearly free.

| file | use |
|---|---|
| `AzureLLMInferenceTrace_conv.csv` | short conversational traffic |
| `AzureLLMInferenceTrace_code.csv` | longer code and document traffic |

Both from https://github.com/Azure/AzurePublicDataset/tree/master/data

Columns: `TIMESTAMP`, `ContextTokens`, `GeneratedTokens`. Download them into
`data/` which is gitignored, since they are large and publicly available.

---

## M1. Read the trace

Parse both CSVs into a list of request records holding arrival time in
seconds, prompt tokens, output tokens, and a stable request id. Normalise so
the first request sits at t=0.

Details that matter later.

- `TIMESTAMP` is a wall clock string, so parse it once and convert to a float
  offset rather than re-parsing per request.
- Keep the records in arrival order and keep the original row index as the id,
  so a surprising flow in a result can be traced back to a trace row.
- Add a `--duration` filter that truncates the trace to the first N seconds.
  A full trace is far longer than any simulation that finishes.
- Decide what happens to a row with zero or missing `ContextTokens`. Dropping
  it is fine, silently treating it as zero is not.

**Done when** the first 100 rows print, and per trace it reports request
count, duration, mean prompt length, p99 prompt length, and mean arrival
rate.

Make the prompt-length histogram here. It goes in the paper and it shows what
transfer sizes are actually involved.

---

## M2. Cache size

```python
BYTES_PER_TOKEN = 2 * layers * kv_heads * head_dim * dtype_bytes
cache_bytes     = prompt_tokens * BYTES_PER_TOKEN
```

The leading 2 is one key tensor plus one value tensor. `kv_heads` is the
number of key and value heads, which is smaller than the attention head count
on any model using grouped query attention.

| model | layers | kv_heads | head_dim | bytes/token | 4K context |
|---|---|---|---|---|---|
| Llama-3-8B | 32 | 8 | 128 | 131,072 | 536.9 MB |
| Llama-3-70B | 80 | 8 | 128 | 327,680 | 1.342 GB |
| Llama-2-13B | 40 | 40 | 128 | 819,200 | 3.355 GB |

Llama-2-13B having a larger cache than Llama-3-70B is not a mistake. The 13B
model has no grouped query attention, so all 40 heads carry key and value
state. It is worth a sentence in the paper because a reader will assume it is
a typo.

Keep the model configs in one dict rather than scattered constants, and derive
bytes per token rather than hardcoding it, so a dtype change to fp8 is a one
line edit.

**Done when** a 4096-token prompt on Llama-3-70B gives exactly
1,342,177,280 bytes.

---

## M3. When the flow starts

The cache does not exist until prefill finishes, so the flow start is not the
arrival time.

```python
flow_start      = arrival + prefill_queue_wait + prefill_compute
prefill_compute = alpha * P + beta * P**2
```

The linear term is the per-token work and the quadratic term is attention over
the prompt. Fit alpha and beta against Vidur, or pick values that put a 4K
prefill near 200 ms and state the choice in the methodology.

The queue wait needs a model of the prefill pool. The simple version is N
instances, each serving one request at a time, FIFO, where an arriving request
takes the instance that frees up soonest. That is a heap and about twenty
lines. It is the difference between a realistic arrival pattern and every flow
starting at the same instant.

Two things to watch.

- Under high load the queue grows without bound and flow starts drift far past
  the end of the simulated window. Report the final queue depth so an
  unstable configuration is visible rather than silent.
- The decode side also occupies an instance for the duration of generation. A
  first pass can ignore this, but note the omission since it understates
  contention for the decode pool.

**Done when** flow start times are spread realistically and prefill queue
depth over time can be plotted.

---

## M4. Placement and sharding

Each request gets a prefill instance and a decode instance. Round robin to
start, since placement becomes its own experimental variable later.

```python
shard = cache_bytes // TP
for i in range(TP):
    emit(node(prefill_inst, i), node(decode_inst, i), start_ps, shard)
```

One logical transfer becomes TP concurrent flows between matched GPU indices,
and the transfer is only complete when the slowest shard lands. That is why
the analysis takes the max over a request's shards rather than the mean.

Keep `node()` a function with a numbering parameter, because that is where
conventional and rail-optimised numbering live.

```python
conventional:  node = server * GPUS_PER_SERVER + gpu
rail:          node = gpu * NUM_SERVERS + server
```

Rail-optimised is a renumbering, not a different topology file. HTSIM maps
hosts to ToRs with `HOST_POD_SWITCH(src) = src / radix_down`, so the host IDs
chosen decide which switch each endpoint hangs off.

Also decide how the prefill and decode pools divide the node space. Splitting
the fabric in half is the obvious start, and it should be a parameter, since a
pool split that puts both ends of every transfer under the same ToR would
quietly remove all the cross-fabric traffic the project is about.

Guard against a request whose prefill and decode instance are the same, which
produces a self-directed flow that HTSIM will reject.

**Done when** one request produces TP lines between matched GPU indices, and
it is clear which node sits on which ToR.

---

## M5. Emit and verify

```
Nodes 128
Connections 4096
0->64 id 1 start 0 size 167772160
```

Alongside it, a metadata file keyed by flow id carrying request id, deadline,
prompt length and shard index. The `.cm` format has nowhere to put deadlines
and they are needed for deadline attainment, so they live outside the file the
simulator reads.

Flow ids must be unique across the whole file, not per request. The simplest
scheme is a single counter, with the metadata file recording which request and
shard each id belongs to.

**Three traps.**

1. `start` is in **picoseconds**. 1 us = 1,000,000 ps. A workload that looks
   1,000,000x too fast is this.
2. The `Connections` header must equal the line count exactly.
3. No single flow may exceed about 2.1 GB, because the size field is a 32-bit
   int. That caps a TP=8 transfer near a 50K context. Fail loudly on a request
   that exceeds it rather than emitting a wrapped value.

**Done when** `./scripts/run.sh roce ft128_1os test <file>.cm` completes and
the flow count comes back right.

Start with ten requests, not ten thousand. A ten-flow file through `run.sh`
catches format errors that would otherwise surface as a mysterious zero-flow
result weeks later.

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

Rescaling inter-arrival times preserves the burstiness of the real trace,
which is the reason for using a trace at all. Resampling arrivals from a
Poisson process would hit the same mean and throw that away.

Write the capacity definition into the docs. Any defensible definition works
but it has to be stated, because every load number in the paper is relative to
it. Whether capacity is counted at the prefill NICs, the decode NICs, or the
narrowest tier of the fabric changes what load 0.7 means, and the
oversubscribed topologies make those three numbers differ.

**Done when** `--load 0.3` and `--load 0.9` give measurably different
completion times on the same topology, and 1:1 against 4:1 finally diverges.

**That divergence is the test that the whole generator worked.** Until the
topology variable moves, there is no way to tell a correct generator from a
broken one.

---

## M7. Validation

Assert inside the generator rather than in a separate script, so a bad
workload cannot reach a result.

- emitted bytes per second matches the target within a few percent
- `Connections` header equals the line count
- no flow over 2.1 GB
- node IDs within range for the target topology
- no self-directed flows
- flow ids unique
- start times monotonically non-decreasing, which makes the file readable
- flow count returned by the simulator matches what was emitted

The last one is the only check that involves actually running HTSIM, and it is
the one that catches a file the simulator parses differently than intended.

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

Every argument that changes the output needs to land in the metadata file too.
A `.cm` file with no record of the load, model and numbering that produced it
is unusable three weeks later.

## Two rules

**Test at every milestone.** Each one has a stated done condition that can be
checked without the next milestone existing.

**Commit generated workloads, not just the generator.** When someone asks in
December why a number looks odd, the exact input that produced it needs to
still exist.

## Shape of the work

M1 through M5 gives a working generator at whatever load the trace happens to
carry. M6 is what unblocks E2, E3 and E5. M7 is small but belongs before the
first real sweep, not after.

M1 and M2 have no dependency on each other. M3 depends on M1. M4 and M5 depend
on M2. M6 depends on M1 and M5. M7 touches all of them.
