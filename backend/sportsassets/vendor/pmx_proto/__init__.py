"""Polymarket Exchange gRPC definitions -- MARKET DATA ONLY.

`marketdatasubscription_pb2` / `marketdatasubscription_pb2_grpc` are generated
from `marketdatasubscription.proto` in this directory (see README.md). No order,
trading, RFQ, position, account or funding service is vendored here, and
tests/test_institutional_md_proto_vendor.py fails if one appears.

Importing this package imports nothing; the generated modules (and grpcio /
protobuf) are imported only by whoever asks for them.
"""
