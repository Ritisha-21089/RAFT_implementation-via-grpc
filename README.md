# Raft Consensus with Leader Leases

A from-scratch implementation of the Raft consensus algorithm with the **leader lease** optimisation, running as a replicated key-value store over gRPC across nodes on separate Google Cloud VMs.

## Why

In textbook Raft, a read cannot simply be answered by the leader. The leader may already have been deposed by a partition it has not noticed yet, so answering from local state risks returning stale data. The standard fix is to confirm leadership with a majority before every read — correct, but it puts a full network round trip on the read path. Across geo-distributed nodes that round trip dominates read latency.

A **leader lease** trades a liveness assumption for that latency. The leader holds a time-bounded lease, propagated on the existing heartbeat, during which no other node can become leader. While the lease is valid the leader can serve linearizable reads from local state with no consensus round at all. The cost is a dependency on bounded clock drift: the safety argument rests on the lease expiring everywhere before a new leader acquires one. This is the same trade-off CockroachDB and YugabyteDB make.

## Design

Each node runs as its own process on its own GCP VM. Nodes talk to each other, and clients talk to nodes, over gRPC with Protocol Buffers.

**Lease propagation.** The leader piggybacks the remaining lease duration on every `AppendEntries` heartbeat. If it fails to renew with a majority before expiry, it steps down rather than continuing to serve reads.

**Election safety.** When a voter responds to `RequestVote`, it reports the longest remaining lease duration it knows about. A newly elected leader waits out that duration before acquiring its own lease, which is what prevents two nodes from believing they hold a valid lease at the same time.

**Persistence.** Each node keeps its own `logs_node_<id>/` directory holding the replicated log, term metadata, and a `dump.txt` trace of state transitions. The log records every write and NO-OP entry with its term:

```
NO-OP 0
SET name1 Jaggu 0
SET name2 Raju 0
SET name3 Bheem 1
```

**Client protocol.** The client holds the address of every node and its current guess at the leader. It sends `GET`/`SET` to that leader; on failure the reply carries the real leader id, and the client retries against it.

## Trade-offs

- **Leases assume bounded clock drift.** If clocks skew further than the lease margin, two nodes can believe they hold the lease and linearizability breaks. Plain Raft has no such assumption. This is a deliberate choice for read latency, not a free win.
- **Reads get faster; writes do not.** Writes still need log replication to a majority. The optimisation only removes the read-path round trip.
- **Fixed cluster membership.** Nodes are configured at startup. There is no joint-consensus reconfiguration, which keeps the election logic small but means the cluster cannot be resized while running.
- **Crash recovery is file-based.** Restarting a node replays its on-disk log rather than fetching a snapshot, so recovery time grows with log length.

## Running it

```bash
# on each VM, one process per node
python node.py <node_id>

# client, from any VM or locally
python client.py
```

Node addresses are configured in the node list; each node writes its own `logs_node_<id>/` directory on start.

## What I would do differently

- No log compaction or snapshotting, so the log grows without bound and restart cost grows with it.
- Lease and election timeouts are hard-coded constants; they should be configurable and tuned to measured inter-node RTT.
- Testing was manual — killing processes and watching `dump.txt`. A deterministic simulation harness that injects partitions and clock skew would be far better evidence that the lease logic is actually correct.
- No metrics. Election frequency and lease renewal failures are the two numbers that would tell you whether the timeouts are tuned sensibly.

## References

- [Raft](https://raft.github.io/) and the [original paper](https://raft.github.io/raft.pdf)
- [Low latency reads in geo-distributed SQL with Raft leader leases](https://www.yugabyte.com/blog/low-latency-reads-in-geo-distributed-sql-with-raft-leader-leases/) — YugabyteDB
- [CockroachDB replication layer](https://www.cockroachlabs.com/docs/v21.1/architecture/replication-layer.html)
