
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
    with grpc.insecure_channel("grpc-server-service.target-services:60065") as channel:
        stub = protofile_pb2_grpc.UsersStub(channel)
        response = stub.GetUsers(protofile_pb2.GetUsersRequest())

        print(response.user)

        name = input("name of the user to describe: ")
        id = input("id of the user to describe: ")
        email = input("email of the user to describe: ")
        phonenumber = int(input("phonenumber of the user to describe: "))

        describe_respone = stub.DescribeUser(protofile_pb2.DescribeUserRequest(
            user = protofile_pb2.User(
                name = name,
                id = id,
                email = email,
                phonenumber = phonenumber,
            )))
        
        print(describe_respone)


if __name__ == "__main__":
    logging.basicConfig()
    run()
