

from concurrent import futures
import logging

import grpc
import protofile_pb2
import protofile_pb2_grpc


class Users(protofile_pb2_grpc.UsersServicer):
    def GetUsers(self, request, context):
        return protofile_pb2.GetUsersResponse(
            user = protofile_pb2.User(
                name="John Doe",
                id='nigga1',
                email='ironavenger10@gmail.com',
                phonenumber=9663304909
            )
        )
    
    def DescribeUser(self, request, context):
        reply  = protofile_pb2.DescribeUserResponse()
        reply.text = f"the name of the user is {request.user.name}, their id is {request.user.id} and their phonenumber is {request.user.phonenumber}"
        return reply


def serve():
    port = "60065"
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    protofile_pb2_grpc.add_UsersServicer_to_server(Users(), server)
    server.add_insecure_port("[::]:" + port)
    server.start()
    print("Server started, listening on " + port)
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig()
    serve()
