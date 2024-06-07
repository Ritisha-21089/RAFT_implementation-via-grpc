import time
import random
import grpc
import raft_pb2
import raft_pb2_grpc
import logging
import sys
import os
import json
from concurrent import futures
from threading import Thread
from dataclasses import dataclass


@dataclass
class RaftNode:
    timeout_time: float = None
    current_role: str = "follower"
    current_leader: int = None
    votes_received: list = None
    sent_log_length: dict = None
    acknowledged_log_length: dict = None
    lease_timer: float = time.time()
    has_lease: bool = False

    def __post_init__(self):
        self.votes_received = []
        self.sent_log_length = {}
        self.acknowledged_log_length = {}



def open_metadata_file():
    with open(metadata_file_path, "r") as metadata_file:
        metadata = json.load(metadata_file)
        current_term = metadata["current_term"]
        voted_for = metadata["voted_for"]
        log_entries = metadata["log_entries"]
        commit_length = metadata["commit_length"]
    return current_term, voted_for, log_entries, commit_length


def write_metadata_file(current_term, voted_for, log_entries, commit_length):
    with open(metadata_file_path, "w") as metadata_file:
        json.dump(
            {
                "current_term": current_term,
                "voted_for": voted_for,
                "log_entries": log_entries,
                "commit_length": commit_length,
            },
            metadata_file,
        )


def reset_election_timeout():
    node_variables.timeout_time = time.time() + random.randint(5, 10)


def commit_log_entries():
    current_term, voted_for, log_entries, commit_length = open_metadata_file()
    while commit_length < len(log_entries):
        acks_commit = sum(
            1
            for i in range(1, 6)
            if node_variables.acknowledged_log_length[i] > commit_length
        )
        if acks_commit >= 3:
            commit_length += 1
            print(
                f'Node {node_id} (leader) committed the entry SET {log_entries[commit_length-1]["key"]} {log_entries[commit_length-1]["value"]} to the state machine'
            )
            with open(dump_file_path, "a") as dump_file:
                dump_file.write(
                    f'Node {node_id} (leader) committed the entry SET {log_entries[commit_length-1]["key"]} {log_entries[commit_length-1]["value"]} to the state machine\n'
                )
        else:
            break

    write_metadata_file(current_term, voted_for, log_entries, commit_length)


def receive_log_response(response):
    follower = response.node_id
    term = response.term
    ack = response.ack
    success = response.status

    current_term, voted_for, log_entries, commit_length = open_metadata_file()

    if term > current_term:
        reset_election_timeout()
        current_term = term
        voted_for = []
        node_variables.current_role = "follower"
        node_variables.current_leader = None
        node_variables.votes_received = []
        write_metadata_file(current_term, voted_for, log_entries, commit_length)
        print(f"Node {node_id} Stepping down")
        with open(dump_file_path, "a") as dump_file:
            dump_file.write(f"Node {node_id} Stepping down\n")

    elif term == current_term and node_variables.current_role == "leader":
        if success and ack >= node_variables.acknowledged_log_length[follower]:
            node_variables.sent_log_length[follower] = ack
            node_variables.acknowledged_log_length[follower] = ack
            commit_log_entries()
        elif node_variables.sent_log_length[follower] > 0:
            node_variables.sent_log_length[follower] -= 1
            replicate_log(node_id, follower, time.time() + 10)


def replicate_log(src, dest, lease_time, no_op=False):
    current_term, voted_for, log_entries, commit_length = open_metadata_file()

    prefix_length = node_variables.sent_log_length[dest]
    suffix = [
        raft_pb2.log_entry(
            key=log_entries[i]["key"],
            value=log_entries[i]["value"],
            term=int(log_entries[i]["term"]),
        )
        for i in range(prefix_length, len(log_entries))
    ]
    prefix_term = 0
    if prefix_length > 0:
        prefix_term = log_entries[prefix_length - 1]["term"]

    with grpc.insecure_channel(CLUSTER_NODES[dest]) as channel:
        stub = raft_pb2_grpc.LeaderFunctionStub(channel)

        try:
            response = stub.log_request(
                raft_pb2.leader_details(
                    leader_id=src,
                    term=current_term,
                    prefix_len=prefix_length,
                    prefix_term=prefix_term,
                    leader_commit=commit_length,
                    suffix=suffix,
                    no_op=str(no_op),
                    lease_time=lease_time,
                )
            )
            receive_log_response(response)
            return 1
        except Exception as e:
            print(f"Error occurred while sending RPC to Node {dest}")
            with open(dump_file_path, "a") as dump_file:
                dump_file.write(f"Error occurred while sending RPC to Node {dest}\n")
            return 0


def append_entries(prefix_len, leader_commit, suffix):
    current_term, voted_for, log_entries, commit_length = open_metadata_file()

    if len(suffix) > 0 and len(log_entries) > prefix_len:
        index = min(len(log_entries), prefix_len + len(suffix)) - 1
        if log_entries[index]["term"] != suffix[index - prefix_len].term:
            log_entries = log_entries[:prefix_len]

    if prefix_len + len(suffix) > len(log_entries):
        log_entries.extend(
            {"term": entry.term, "key": entry.key, "value": entry.value}
            for entry in suffix[len(log_entries) - prefix_len :]
        )

    with open(log_file_path, "w") as log_file:
        for entry in log_entries:
            if entry["key"] == "NO_OP":
                log_file.write(f'NO_OP {entry["term"]}\n')
            else:
                log_file.write(f'SET {entry["key"]} {entry["value"]} {entry["term"]}\n')

    if leader_commit > commit_length:
        non_committed_entries = log_entries[commit_length:leader_commit]
        for entry in non_committed_entries:
            print(
                f'Node {node_id} (follower) committed the entry SET {entry["key"]} {entry["value"]} to the state machine'
            )
            with open(dump_file_path, "a") as dump_file:
                dump_file.write(
                    f'Node {node_id} (follower) committed the entry SET {entry["key"]} {entry["value"]} to the state machine\n'
                )
        commit_length = leader_commit

    write_metadata_file(current_term, voted_for, log_entries, commit_length)


def recv_vote_response(response):
    current_term, voted_for, log_entries, commit_length = open_metadata_file()

    voter_id = response.node_id
    term = response.term
    vote_granted = response.granted
    if response.lease_time > node_variables.lease_timer:
        node_variables.lease_timer = response.lease_time

    if (
        node_variables.current_role == "candidate"
        and term == current_term
        and vote_granted
        and voter_id not in node_variables.votes_received
    ):
        node_variables.votes_received.append(voter_id)
        if len(node_variables.votes_received) >= 3:
            node_variables.current_role = "leader"
            node_variables.current_leader = node_id
            reset_election_timeout()
            print(f"Node {node_id} became the leader for term {current_term}")
            with open(dump_file_path, "a") as dump_file:
                dump_file.write(
                    f"Node {node_id} became the leader for term {current_term}\n"
                )

            printed_once = False
            while time.time() < node_variables.lease_timer:
                if not printed_once:
                    print(
                        f"New Leader {node_id} waiting for the Old Leader Lease to timeout"
                    )
                    with open(dump_file_path, "a") as dump_file:
                        dump_file.write(
                            f"New Leader {node_id} waiting for the Old Leader Lease to timeout\n"
                        )
                    printed_once = True
                time.sleep(0.1)

            no_op_key = "NO_OP"
            no_op_value = "NO_OP"
            log_entries.append(
                {"term": current_term, "key": no_op_key, "value": no_op_value}
            )
            write_metadata_file(current_term, voted_for, log_entries, commit_length)
            node_variables.acknowledged_log_length[node_id] = len(log_entries)
            node_variables.has_lease = True
            start_time = time.time()
            for i in range(1, 6):
                if i != node_id:
                    node_variables.sent_log_length[i] = len(log_entries)
                    node_variables.acknowledged_log_length[i] = 0
                    replicate_log(node_id, i, float(start_time + 10), True)

    elif term > current_term:
        reset_election_timeout()
        node_variables.current_role = "follower"
        node_variables.current_leader = None
        node_variables.votes_received = []
        current_term = term
        voted_for = []
        write_metadata_file(current_term, voted_for, log_entries, commit_length)
        print(f"{node_id} Stepping down")
        with open(dump_file_path, "a") as dump_file:
            dump_file.write(f"{node_id} Stepping down\n")


def election_timer_and_vote_request():
    while True:
        with open(metadata_file_path, "r") as metadata_file:
            try:
                metadata = json.load(metadata_file)
                current_term = metadata["current_term"]
                voted_for = metadata["voted_for"]
                log_entries = metadata["log_entries"]
                commit_length = metadata["commit_length"]
            except Exception as e:
                time.sleep(0.1)
                continue

        if (
            time.time() > node_variables.timeout_time
            and node_variables.current_role
            in {
                "follower",
                "candidate",
            }
        ):
            print(f"Node {node_id} election timer timed out, Starting election")
            with open(dump_file_path, "a") as dump_file:
                dump_file.write(
                    f"Node {node_id} election timer timed out, Starting election\n"
                )
            reset_election_timeout()
            node_variables.current_role = "candidate"
            current_term += 1
            voted_for = [node_id]
            node_variables.votes_received = [node_id]
            node_variables.has_lease = False
            write_metadata_file(current_term, voted_for, log_entries, commit_length)
            last_term = 0 if len(log_entries) == 0 else log_entries[-1]["term"]

            for i in range(1, 6):
                if i != node_id:
                    with grpc.insecure_channel(CLUSTER_NODES[i]) as channel:
                        stub = raft_pb2_grpc.CandidateFunctionStub(channel)
                        try:
                            response = stub.vote_request(
                                raft_pb2.voter_details(
                                    node_id=node_id,
                                    current_term=current_term,
                                    log_length=len(log_entries),
                                    last_term=last_term,
                                )
                            )
                            recv_vote_response(response)
                        except Exception as e:
                            print(f"Error occurred while sending RPC to Node {i}")
                            pass

        time.sleep(0.1)


def heartbeat():
    while True:
        if node_variables.current_role == "leader" and node_variables.has_lease:
            print(f"Leader {node_id} sending heartbeat & Renewing Lease")
            with open(dump_file_path, "a") as dump_file:
                dump_file.write(
                    f"Leader {node_id} sending heartbeat & Renewing Leasee\n"
                )

            current_term, voted_for, log_entries, commit_length = open_metadata_file()
            heartbeat_acks = 0
            start_time = time.time()
            for i in range(1, 6):
                if i != node_id:
                    heartbeat_acks += replicate_log(node_id, i, float(start_time + 10))
            if heartbeat_acks >= 2:
                node_variables.lease_timer = start_time + 10
            elif time.time() > node_variables.lease_timer:
                print(f"Leader {node_id} lease renewal failed. Stepping Down.")
                with open(dump_file_path, "a") as dump_file:
                    dump_file.write(
                        f"Leader {node_id} lease renewal failed. Stepping Down.\n"
                    )
                reset_election_timeout()
                node_variables.current_role = "follower"
                node_variables.current_leader = None
                node_variables.votes_received = []
                voted_for = []
                write_metadata_file(current_term, voted_for, log_entries, commit_length)
                node_variables.has_lease = False
        time.sleep(1)


class CandidateFunction(raft_pb2_grpc.CandidateFunctionServicer):
    def vote_request(self, request, context):

        candidate_id = request.node_id
        candidate_term = request.current_term
        candidate_log_length = request.log_length
        candidate_last_term = request.last_term

        current_term, voted_for, log_entries, commit_length = open_metadata_file()

        if candidate_term > current_term:
            reset_election_timeout()
            current_term = candidate_term
            node_variables.current_role = "follower"
            node_variables.current_leader = None
            node_variables.votes_received = []
            voted_for = []
            write_metadata_file(current_term, voted_for, log_entries, commit_length)

        last_term = 0 if len(log_entries) == 0 else log_entries[-1]["term"]
        log_ok = candidate_last_term > last_term or (
            candidate_last_term == last_term
            and candidate_log_length >= len(log_entries)
        )

        if (
            (candidate_term == current_term)
            and (voted_for == [] or voted_for[0] == candidate_id)
            and log_ok
        ):

            voted_for = [candidate_id]
            reset_election_timeout()
            write_metadata_file(current_term, voted_for, log_entries, commit_length)
            print(f"Vote granted for Node {candidate_id} in term {candidate_term}")
            with open(dump_file_path, "a") as dump_file:
                dump_file.write(
                    f"Vote granted for Node {candidate_id} in term {candidate_term}\n"
                )
            return raft_pb2.voter_response(
                node_id=node_id,
                term=current_term,
                granted=True,
                lease_time=float(node_variables.lease_timer),
            )
        else:
            print(f"Vote denied for Node {candidate_id} in term {candidate_term}")
            with open(dump_file_path, "a") as dump_file:
                dump_file.write(
                    f"Vote denied for Node {candidate_id} in term {candidate_term}\n"
                )
            return raft_pb2.voter_response(
                node_id=node_id,
                term=current_term,
                granted=False,
                lease_time=float(node_variables.lease_timer),
            )


class LeaderFunction(raft_pb2_grpc.LeaderFunctionServicer):
    def log_request(self, request, context):
        leader_id = request.leader_id
        term = request.term
        prefix_len = request.prefix_len
        prefix_term = request.prefix_term
        leader_commit = request.leader_commit
        suffix = request.suffix
        lease_time = request.lease_time

        current_term, voted_for, log_entries, commit_length = open_metadata_file()

        if term > current_term:
            reset_election_timeout()
            current_term = term
            voted_for = []
            node_variables.votes_received = []
            write_metadata_file(current_term, voted_for, log_entries, commit_length)

        elif term == current_term:
            reset_election_timeout()
            node_variables.current_role = "follower"
            node_variables.current_leader = leader_id
            node_variables.has_lease = False
            node_variables.lease_timer = lease_time

        log_ok = (len(log_entries) >= prefix_len) and (
            prefix_len == 0 or log_entries[prefix_len - 1]["term"] == prefix_term
        )

        if term == current_term and log_ok:
            print(f"Node {node_id} accepted AppendEntries RPC from leader {leader_id}")
            with open(dump_file_path, "a") as dump_file:
                dump_file.write(
                    f"Node {node_id} accepted AppendEntries RPC from leader {leader_id}\n"
                )
            append_entries(prefix_len, leader_commit, suffix)
            ack = prefix_len + len(suffix)
            return raft_pb2.log_response(
                node_id=node_id, term=current_term, ack=ack, status=True
            )
        else:
            print(f"Node {node_id} rejected AppendEntries RPC from leader {leader_id}")
            with open(dump_file_path, "a") as dump_file:
                dump_file.write(
                    f"Node {node_id} rejected AppendEntries RPC from leader {leader_id}\n"
                )
            return raft_pb2.log_response(
                node_id=node_id, term=current_term, ack=0, status=False
            )


class ClientFunction(raft_pb2_grpc.ClientFunctionServicer):
    def set_pair(self, request, context):
        key = request.key
        value = request.value
        if node_variables.current_role == "leader":
            print(f"Node {node_id} (leader) received an entry SET {key} {value}")
            with open(dump_file_path, "a") as dump_file:
                dump_file.write(
                    f"Node {node_id} (leader) received an entry SET {key} {value}\n"
                )
            if not node_variables.has_lease:
                print(f"Leader {node_id} does not have the lease.")

        if node_variables.current_role == "leader" and node_variables.has_lease:
            current_term, voted_for, log_entries, commit_length = open_metadata_file()

            log_entries.append({"term": current_term, "key": key, "value": value})
            write_metadata_file(current_term, voted_for, log_entries, commit_length)
            node_variables.acknowledged_log_length[node_id] = len(log_entries)
            successfully_replicated = 1

            start_time = time.time()
            for i in range(1, 6):
                if i != node_id:
                    successfully_replicated += replicate_log(
                        node_id, i, float(start_time + 10)
                    )
            if successfully_replicated >= 3:
                node_variables.lease_timer = start_time + 10
                return raft_pb2.server_response(
                    data="None", leader_id=node_id, status="Done"
                )
            else:
                return raft_pb2.server_response(
                    data="None", leader_id=node_variables.current_leader, status="Fail"
                )
        else:
            return raft_pb2.server_response(
                data="None", leader_id=node_variables.current_leader, status="Fail"
            )

    def get_pair(self, request, context):
        key = request.key

        current_term, voted_for, log_entries, commit_length = open_metadata_file()

        if node_variables.current_leader == "leader":
            print(f"Node {node_id} (leader) received an entry GET {key}")
            with open(dump_file_path, "a") as dump_file:
                dump_file.write(
                    f"Node {node_id} (leader) received an entry GET {key}\n"
                )
            if not node_variables.has_lease:
                print(f"Leader {node_id} does not have the lease.")

        if node_variables.current_role == "leader" and node_variables.has_lease:
            for i in range(len(log_entries) - 1, -1, -1):
                if log_entries[i]["key"] == key:
                    return raft_pb2.server_response(
                        data=log_entries[i]["value"], leader_id=node_id, status="Done"
                    )
            return raft_pb2.server_response(
                data="None", leader_id=node_id, status="Done"
            )
        else:
            return raft_pb2.server_response(
                data="None", leader_id=node_variables.current_leader, status="Fail"
            )


def initialize_metadata():
    if os.path.exists(metadata_file_path):
        current_term, voted_for, log_entries, commit_length = open_metadata_file()
    else:
        current_term = 0
        voted_for = []
        log_entries = []
        commit_length = 0
        write_metadata_file(current_term, voted_for, log_entries, commit_length)
    return 


def main():
    global node_id, node_variables, log_file_path, dump_file_path, metadata_file_path, CLUSTER_NODES
  
    CLUSTER_NODES = {
        1: "localhost:50051",
        2: "localhost:50052",
        3: "localhost:50053",
        4: "localhost:50054",
        5: "localhost:50055",
    }

    if len(sys.argv) < 2:
        # print("Missing command-line argument for node ID.")
        print("Missing command-line argument for node ID.\n Try: python3 node.py <node_id>")
        sys.exit(1)  # Exit the script with an error code

    try:
        node_id = int(sys.argv[1])
    except ValueError:
        print("Node ID must be an integer.")
        sys.exit(1)

    log_directory = f"logs_node_{node_id}"
    if not os.path.exists(log_directory):
        os.makedirs(log_directory)

    node_variables = RaftNode()

    log_file_path = f"{log_directory}/log.txt"
    dump_file_path = f"{log_directory}/dump.txt"
    metadata_file_path = f"{log_directory}/metadata.txt"

    initialize_metadata()
    reset_election_timeout()
    t1 = Thread(target=election_timer_and_vote_request)
    t1.start()
    t2 = Thread(target=heartbeat)
    t2.start()
    logging.basicConfig()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    raft_pb2_grpc.add_CandidateFunctionServicer_to_server(CandidateFunction(), server)
    raft_pb2_grpc.add_ClientFunctionServicer_to_server(ClientFunction(), server)
    raft_pb2_grpc.add_LeaderFunctionServicer_to_server(LeaderFunction(), server)
    node_address = CLUSTER_NODES.get(node_id)
    if node_address:
        server.add_insecure_port(node_address)
    else:
        print(f"Error: Define a corresponding server for Node {node_id}.")
    server.start()
    server.wait_for_termination()

if __name__ == "__main__":
    main()

