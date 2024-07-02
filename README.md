# RAFT Application

This projec focuses on implementing a modified Raft system similar to those used by geo-distributed Database clusters such as CockroachDB or YugabyteDB. Raft is a consensus algorithm designed for distributed systems to ensure fault tolerance and consistency. It operates through leader election, log replication, and commitment of entries across a cluster of nodes.

We aim to build a database that stores key-value pairs mapping string (key) to string (value). The Raft cluster maintains this database and ensures fault tolerance and strong consistency. The client requests the server to perform operations on this database reliably.

### Resources
- [Raft (Main Algorithm)](https://raft.github.io/)
- [Original Paper](https://raft.github.io/raft.pdf)
- [Medium Explanations Part 1](https://medium.com/@eugene.lai/raft-consensus-algorithm-part-1-914b062da2b)
- [Medium Explanations Part 2](https://medium.com/@eugene.lai/raft-consensus-algorithm-part-2-8fbbeb7f9b6d)
- [Raft Visualization](https://raft.github.io/raftscope/index.html)
- [Low Latency Reads in Geo-Distributed SQL with Raft Leader Leases](https://www.yugabyte.com/blog/low-latency-reads-in-geo-distributed-sql-with-raft-leader-leases/)
- [Replication Layer (cockroachlabs.com)](https://www.cockroachlabs.com/docs/v21.1/architecture/replication-layer.html)

## Overview

In this project we have implemented the Raft algorithm with the leader lease modification. Each node will be a process hosted on a separate Virtual Machine on Google Cloud, and the client can reside either in Google Cloud’s Virtual Machine or in the local machine.   I have used gRPC for communication between nodes along with client-node interaction. 

### Raft Modification (for faster Reads)
Leader Lease: A time-based “lease” for Raft leadership that gets propagated through the heartbeat mechanism. If we have well-behaved clocks, we can obtain linearizable reads without paying a round-trip latency penalty. This is achieved using the concept of Leases.

### Implementation Details

#### 1. Pseudo Code
Refer to the pseudo code (pg 60 to 66) while implementing to handle edge cases correctly. This [lecture video](https://youtu.be/u-mNf9Rt7mw) explains the same.

#### 2. Storage and Database Operations
- **Persistent Logs:** Store logs, metadata, and dump files in a directory named `logs_node_x` where x is the node ID.
- **Log Format:** Store all WRITE OPERATIONS and NO-OP operations along with the term number.

Example log.txt file:
```
NO-OP 0
SET name1 Jaggu 0
SET name2 Raju 0
SET name3 Bheem 1
```

#### 3. Client Interaction
- **Leader Information:** Stores the IP addresses and ports of all nodes and the current leader ID.
- **Request Server:** Sends GET/SET requests to the leader node and handles failures by updating the leader ID and resending the request.

RPC & Protobuf for the client:
```proto
rpc ServeClient (ServeClientArgs) returns (ServeClientReply) {}

message ServeClientArgs {
  string Request = 1;
}

message ServeClientReply {
  string Data = 1;
  string LeaderID = 2;
  bool Success = 3;
}
```

#### 4. Standard Raft RPCs
- **AppendEntry:** Used for heartbeats and log replication, must send the lease interval duration.
- **RequestVote:** Voters must propagate the longest remaining duration of the old leader’s lease.

#### 5. Election Functionalities
- **Start Election:** Nodes start an election if no event is received within the election timeout.
- **Receive Voting Request:** Nodes vote for candidates based on specific conditions.
- **Leader State:** New leaders wait for the old leader's lease timeout before acquiring their own lease.

#### 6. Log Replication Functionalities
- **Periodic Heartbeats:** Sent by the leader to maintain its state and reacquire the lease.
- **Replicate Log Request:** Synchronizes follower's logs with the leader's.
- **Replicate Log Reply:** Nodes accept AppendEntriesRPC based on certain conditions.

#### 7. Committing Entries
- **Leader Committing an Entry:** Majority of nodes must acknowledge appending the entry.
- **Follower Committing an Entry:** Use LeaderCommit field in the AppendEntry RPC.

#### 8. Print Statements & Dump.txt
The dump.txt file collects and stores necessary print statements for debugging and state tracking. Example print statements:
- Leader sending heartbeats: "Leader {NodeID} sending heartbeat & Renewing Lease"
- Leader lease renewal failed: "Leader {NodeID} lease renewal failed. Stepping Down."
- Node starting election: “Node {NodeID} election timer timed out, Starting election."
- Node becoming leader: "Node {NodeID} became the leader for term {TermNumber}."
- Follower node committed entry: "Node {NodeID} (follower) committed the entry {entry operation} to the state machine."

## Assumptions
- Create one cluster of the database using Raft with a fixed number of nodes from the start.
- Use interrupts to stop a node/program and restart the node by executing the program again.

## Instructions
1. To simulate, Run each node as a separate process on different VMs on Google Cloud.
