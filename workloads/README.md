# Traffic matrices

HTSIM connection-matrix format, one line per flow:

    <src>-><dst> id <N> start <picoseconds> size <bytes>

An optional `prio <N>` is parsed, but set_priority is currently commented out
in the transport, so priority has no effect yet.

## kv_llama70b_4k_tp8.cm

One Llama-3-70B KV cache for a 4096-token prompt, sharded at tensor-parallel
degree 8 into eight concurrent flows between matched GPU index pairs.

    bytes/token = 2 * 80 layers * 8 kv_heads * 128 head_dim * 2 bytes = 327,680
    4K cache    = 4096 * 327,680 = 1,342,177,280 bytes (1.34 GB)
    per shard   = 167,772,160 bytes

Theoretical floor per shard at 400 Gbps = 167772160*8/400e9 = 3,355 us.

## kv_incast_8to1.cm

Same bytes, but all eight shards converge on node 8. Bandwidth-bound: the
whole 1.34 GB crosses one 400 Gbps downlink, so the floor is 26,843 us no
matter what the congestion control does.

## Caution

The size field is a 32-bit int, so a single flow cannot exceed ~2.1 GB.
At TP=8 that caps you around a 50K-token context on Llama-3-70B.
