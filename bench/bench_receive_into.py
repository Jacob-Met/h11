"""Compare h11's classic bytes-copying and writable-buffer receive paths.

For an incumbent comparison, run with PYTHONPATH pointing to an unmodified
checkout of h11; that checkout supports only --mode classic. The BytesIO
input models a read/readinto producer without network scheduling jitter.
"""

import argparse
import gc
import io
import statistics
import time

import h11


def run(wire, block_size, mode):
    src = io.BytesIO(wire)
    conn = h11.Connection(h11.SERVER)
    received_data = 0
    while True:
        if mode == "buffered":
            view = conn.get_buffer(block_size)
            limited = view[:block_size]
            size = src.readinto(limited)
            limited.release()
            conn.buffer_updated(size)
        else:
            data = src.read(block_size)
            size = len(data)
            if data:
                conn.receive_data(data)
        if not size:
            break
        while True:
            event = conn.next_event()
            if event in (h11.NEED_DATA, h11.PAUSED):
                break
            if isinstance(event, h11.Data):
                received_data += len(event.data)
    assert received_data == len(wire) - wire.index(b"\r\n\r\n") - 4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("classic", "buffered"), required=True)
    parser.add_argument("--block-size", type=int, default=65536)
    parser.add_argument("--payload-mib", type=int, default=16)
    parser.add_argument("--runs", type=int, default=15)
    args = parser.parse_args()

    payload_length = args.payload_mib * 1024 * 1024
    wire = (
        b"POST / HTTP/1.1\r\nHost: example.test\r\nContent-Length: "
        + str(payload_length).encode()
        + b"\r\n\r\n"
        + b"x" * payload_length
    )
    for _ in range(3):
        run(wire, args.block_size, args.mode)

    samples = []
    for _ in range(args.runs):
        gc.collect()
        start = time.perf_counter()
        run(wire, args.block_size, args.mode)
        samples.append(time.perf_counter() - start)

    median = statistics.median(samples)
    print(
        "mode={} size_mib={} block={} median_ms={:.3f} throughput_MiB_s={:.1f}".format(
            args.mode,
            args.payload_mib,
            args.block_size,
            median * 1000,
            args.payload_mib / median,
        )
    )


if __name__ == "__main__":
    main()
