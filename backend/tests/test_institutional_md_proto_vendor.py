"""THE VENDORED MARKET-DATA PROTO IS THE VENUE'S, FIELD FOR FIELD.

  * the upstream file is byte-identical to the bundle entry (sha256 printed by
    the runner that fetched the official zip)
  * every message and field the compiled file defines -- name, number, type,
    label -- is the upstream file's; the two definitions copied from
    refdata.proto / types.proto match the venue's published values
  * the committed stubs are exactly what grpc_tools.protoc generates from it
  * the stubs import against the locked grpcio/protobuf
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import shutil
import subprocess
import sys

from sportsassets.vendor.pmx_proto import marketdatasubscription_pb2 as pb2

BACKEND = pathlib.Path(__file__).resolve().parents[1]
VENDOR = BACKEND / "sportsassets" / "vendor" / "pmx_proto"
UPSTREAM = VENDOR / "upstream" / "marketdatasubscription.proto"
DERIVED = VENDOR / "marketdatasubscription.proto"

UPSTREAM_SHA256 = (
    "de43bd1c904925c41931b9c8f2268d4e8f2affd05965729e8846f5155a238118")

# InstrumentState as the venue publishes it: refdata.proto in the bundle,
# and the table on /streaming-endpoints/market-data-stream (capture
# 2026-09-16, sha256 ec63d17a...).
INSTRUMENT_STATE = {
    "INSTRUMENT_STATE_CLOSED": 0, "INSTRUMENT_STATE_OPEN": 1,
    "INSTRUMENT_STATE_PREOPEN": 2, "INSTRUMENT_STATE_SUSPENDED": 3,
    "INSTRUMENT_STATE_EXPIRED": 4, "INSTRUMENT_STATE_TERMINATED": 5,
    "INSTRUMENT_STATE_HALTED": 6,
    "INSTRUMENT_STATE_MATCH_AND_CLOSE_AUCTION": 7,
    "INSTRUMENT_STATE_PENDING": 8,
}

_TYPE = {1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32",
         8: "bool", 9: "string", 12: "bytes", 13: "uint32"}


def _strip_comments(text: str) -> str:
    return re.sub(r"//[^\n]*", "", text)


def _messages(text: str) -> dict:
    """message name -> {field name: (label, type, number)}, from proto
    source. Handles oneof blocks (fields inside are plain, no label)."""
    text = _strip_comments(text)
    out = {}
    for m in re.finditer(r"\bmessage\s+(\w+)\s*\{", text):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        body = text[m.end():i - 1]
        fields = {}
        for f in re.finditer(
                r"(?:(optional|repeated)\s+)?([\w.]+)\s+(\w+)\s*=\s*(\d+)\s*;",
                body):
            label, typ, name, num = f.groups()
            fields[name] = (label or "", typ.split(".")[-1], int(num))
        out[m.group(1)] = fields
    return out


def _descriptor_fields(desc) -> dict:
    out = {}
    for f in desc.fields:
        if f.message_type is not None:
            typ = f.message_type.name
        elif f.enum_type is not None:
            typ = f.enum_type.name
        else:
            typ = _TYPE[f.type]
        if f.is_repeated:
            label = "repeated"
        elif f.has_presence and f.containing_oneof is not None and \
                f.containing_oneof.name.startswith("_"):
            label = "optional"           # proto3 `optional` (synthetic oneof)
        else:
            label = ""
        out[f.name] = (label, typ, f.number)
    return out


def test_the_upstream_file_is_the_bundle_entry_byte_for_byte():
    assert hashlib.sha256(UPSTREAM.read_bytes()).hexdigest() == UPSTREAM_SHA256


def test_every_compiled_message_and_field_is_the_venues():
    upstream = _messages(UPSTREAM.read_text())
    fd = pb2.DESCRIPTOR
    assert fd.package == "polymarket.v1"
    names = set(fd.message_types_by_name)
    # Heartbeat comes from types.proto (an empty message); everything else
    # is the upstream marketdatasubscription.proto, all of it.
    assert names == set(upstream) | {"Heartbeat"}
    assert fd.message_types_by_name["Heartbeat"].fields == []
    for name, fields in upstream.items():
        got = _descriptor_fields(fd.message_types_by_name[name])
        assert got == fields, name


def test_the_oneofs_are_the_venues():
    req = pb2.BiDirectionalStreamMarketDataRequest.DESCRIPTOR
    assert [f.name for f in req.oneofs_by_name["command"].fields] == [
        "subscribe", "unsubscribe", "keepalive"]
    resp = pb2.BiDirectionalStreamMarketDataResponse.DESCRIPTOR
    assert [f.name for f in resp.oneofs_by_name["event"].fields] == [
        "heartbeat", "update", "subscription_ack", "subscription_error"]


def test_the_copied_enum_is_the_venues():
    fd = pb2.DESCRIPTOR
    assert set(fd.enum_types_by_name) == {"InstrumentState"}
    got = {v.name: v.number
           for v in fd.enum_types_by_name["InstrumentState"].values}
    assert got == INSTRUMENT_STATE


def test_the_compiled_file_imports_only_the_well_known_timestamp():
    imports = re.findall(r'^\s*import\s+"([^"]+)"\s*;',
                         DERIVED.read_text(), re.M)
    assert imports == ["google/protobuf/timestamp.proto"]
    assert [d.name for d in pb2.DESCRIPTOR.dependencies] == [
        "google/protobuf/timestamp.proto"]


def test_the_committed_stubs_are_what_protoc_generates(tmp_path):
    import grpc_tools  # noqa: F401  -- dev dependency, pinned in pyproject
    rel = "sportsassets/vendor/pmx_proto/marketdatasubscription.proto"
    shutil.copytree(VENDOR, tmp_path / "sportsassets" / "vendor" / "pmx_proto")
    out = subprocess.run(
        [sys.executable, "-m", "grpc_tools.protoc", "--proto_path=.",
         "--python_out=.", "--grpc_python_out=.", rel],
        cwd=tmp_path, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    for name in ("marketdatasubscription_pb2.py",
                 "marketdatasubscription_pb2_grpc.py"):
        regenerated = (tmp_path / "sportsassets" / "vendor" / "pmx_proto"
                       / name).read_text()
        assert regenerated == (VENDOR / name).read_text(), name


def test_the_stubs_import_against_the_locked_runtime():
    import grpc
    import google.protobuf
    from sportsassets import runtime_manifest as RM
    from sportsassets.vendor.pmx_proto import marketdatasubscription_pb2_grpc
    lock = RM.read_lock(BACKEND / "requirements.lock")
    assert lock["grpcio"] == grpc.__version__
    assert lock["protobuf"] == google.protobuf.__version__
    assert hasattr(marketdatasubscription_pb2_grpc,
                   "MarketDataSubscriptionAPIStub")


def test_the_image_ships_the_vendored_package():
    df = (BACKEND / "Dockerfile").read_text()
    assert "COPY backend/sportsassets ./sportsassets" in df
    for name in ("__init__.py", "marketdatasubscription_pb2.py",
                 "marketdatasubscription_pb2_grpc.py"):
        assert (VENDOR / name).is_file()
    assert (VENDOR.parent / "__init__.py").is_file()
