# Polymarket Exchange gRPC: market data only

## Source

The venue's official proto bundle, "Polymarket - Proto Files.zip". It is the
"Download Proto Files" link on
https://docs.polymarket.us/streaming-endpoints/proto-reference and on
https://docs.polymarket.us/grpc-api/overview:

    https://drive.google.com/uc?export=download&id=1oT9gaeBEn0vukHD9GOoj_YvzPnR3otng

It was fetched on 2026-10-03 at 14:13Z and 14:14Z on a GitHub runner, using the
repository's read-only `fetch-docs` workflow (runs 37128882963 and
37128947106, branch `claude/cand21-mdstream`). That workflow sends a GET with
no credentials and writes nothing. The session container cannot reach
docs.polymarket.us or Google Drive.

| | sha256 |
|---|---|
| zip, 61,786 bytes | `52236f65d71e38a8d2258d427a1df0c2599c591720d52fecc0600478fed91ad4` |
| `api/polymarket/v1/marketdatasubscription.proto`, 8,525 bytes | `de43bd1c904925c41931b9c8f2268d4e8f2affd05965729e8846f5155a238118` |
| `api/polymarket/v1/refdata.proto`, 14,698 bytes (enum `InstrumentState` only) | `6c1f131bb25b11fb61a4e760bb7cebc76fd3dd4b29be1e04cfb3b802e3095351` |
| `api/polymarket/v1/types.proto`, 1,705 bytes (message `Heartbeat` only) | `840c68f6b20b83766356b07bbcb82b041f1cabce581b55d7c864b083da668af5` |

All three files were rebuilt byte for byte from the job log and checked
against the sha256 the runner printed for each zip entry.

The docs capture sealed in the repository (branch
`beta48-capability/docs-35047389408`, captured 2026-09-16,
`proto-reference.md` sha256
`ed66609af7ab8d032d247f6e5ba6a88abf1c7b5d2cf2d99e0dc647ce0f8cf64d`) gives field
numbers only for the bidirectional request and response, the subscribe,
unsubscribe and keepalive commands, `SubscriptionAck` and
`SubscriptionError`. It gives none for `MarketDataUpdate`, `BookEntry` or
`InstrumentStats`, so a stream cannot be decoded from it. Every wire number
here comes from the bundle. None was reconstructed or guessed.

## Files

* `upstream/marketdatasubscription.proto`: the bundle file, verbatim (sha256
  above). It is kept for reference and not compiled, because it imports
  REST-gateway annotation protos that are deliberately not vendored.
* `marketdatasubscription.proto`: the compiled file, built from the upstream
  file. Every message, field name, number, type and label is copied. Three
  things were removed:
  * the `google/api` and `protoc-gen-openapiv2` imports and the swagger file
    option. These are REST-gateway annotations and do not affect the gRPC
    wire.
  * the imports of `refdata.proto` and `types.proto`. The only two
    definitions used from them (`InstrumentState`, `Heartbeat`) are copied
    in verbatim.

  The package stays `polymarket.v1`. The RPC paths
  (`/polymarket.v1.MarketDataSubscriptionAPI/BiDirectionalStreamMarketData`)
  and the full type names are therefore the venue's own.
* `marketdatasubscription_pb2.py` and `marketdatasubscription_pb2_grpc.py`:
  generated stubs, committed.

## Why the stubs are committed rather than generated in the Dockerfile

The image installs `requirements.lock` and then the package with `--no-deps`,
so it resolves nothing at build time. Running protoc in the image would add
`grpcio-tools` and `setuptools` to the runtime closure. The generated output
would also no longer be in review. The committed stubs are deterministic, and
`tests/test_institutional_md_proto_vendor.py` regenerates them whenever
`grpcio-tools` is installed and fails on any difference.

To regenerate (with `grpcio-tools==1.84.0`, matching the locked
`grpcio==1.84.0` / `protobuf==7.36.2`), run from `backend/`:

    python -m grpc_tools.protoc --proto_path=. --python_out=. --grpc_python_out=. \
        sportsassets/vendor/pmx_proto/marketdatasubscription.proto

## Deliberately NOT vendored

These are not vendored: `trading.proto` (`OrderEntryAPI`: `InsertOrder`,
`CancelOrder`, `PreviewOrder`, ...), `orders.proto`, `rfq.proto`,
`combo.proto`, `positions.proto`, `funding.proto`, `accounts.proto`,
`dropcopy.proto`, `aeropay.proto`, `checkout.proto`, `kyc.proto`,
`users.proto`, `valuation.proto`, `incentives.proto`, `participant.proto`,
`health.proto`, `orderbook.proto`, `enums.proto`, the rest of
`refdata.proto`, the settlement service, and the `google/api` and openapiv2
annotation protos.

The production PMX credential holds `write:orders`. Nothing in this package
can express an order. The only service it defines is
`polymarket.v1.MarketDataSubscriptionAPI`, and a test fails if any other
service, or any order, trade, cancel or replace name, appears.
