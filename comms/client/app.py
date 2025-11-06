
from __future__ import print_function

import logging

import grpc
import protofile_pb2
import protofile_pb2_grpc


def run():
    # NOTE(gRPC Python Team): .close() is possible on a channel and should be
    # used in circumstances in which the with statement does not fit the needs
    # of the code.
    print("Will try to get users ...")
    with grpc.insecure_channel("localhost:50051") as channel:
        stub = protofile_pb2_grpc.UsersStub(channel)
        response = stub.GetUsers(protofile_pb2.GetUsersRequest())
    print(response.user)


if __name__ == "__main__":
    logging.basicConfig()
    run()
