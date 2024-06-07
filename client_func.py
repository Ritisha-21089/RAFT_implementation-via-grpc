from __future__ import print_function

import logging
import grpc

import raft_pb2_grpc
import raft_pb2

# Define node addresses for local testing
NODES = {
    1: "localhost:50051",
    2: "localhost:50052",
    3: "localhost:50053",
    4: "localhost:50054",
    5: "localhost:50055",
}

def perform_operation(leader_id):
    """
    Perform get or set operation based on user input.
    """
    # Get operation from the user
    print("\nWhich operation do you want to perform?")
    print("1. Get")
    print("2. Set")
    print("3. Exit")
    operation = int(input("Enter the operation number: "))

    # Check for exit condition
    if operation == 3:
        print("Exiting...")
        return False  # Indicates the loop should break

    # Establish gRPC channel with the specified node
    with grpc.insecure_channel(NODES[leader_id]) as channel:
        stub = raft_pb2_grpc.ClientFunctionStub(channel)

        # Perform "Get" operation
        if operation == 1:
            key = input("Enter the key: ")
            response = stub.get_pair(raft_pb2.key(key=key))

        # Perform "Set" operation
        elif operation == 2:
            key = input("Enter the key: ")
            value = input("Enter the value: ")
            response = stub.set_pair(raft_pb2.key_value(key=key, value=value))

        # Handle response
        if response.status == "Done":
            print("Operation successful.")
            if operation == 1:
                print("The KEY's value is: ", response.data if response.data != "None" else "KEY not found.")
        else:
            print(f"Incorrect leader. The leader of the cluster can be: {response.leader_id}")
    
    return True  # Indicates the loop should continue

if __name__ == "__main__":
    logging.basicConfig()
    leader_id = int(input("Enter the Node ID of leader: "))
    
    # Loop until the user decides to exit
    while True:
        if not perform_operation(leader_id):
            break
