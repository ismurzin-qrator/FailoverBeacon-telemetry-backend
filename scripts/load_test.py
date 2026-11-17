import argparse
import asyncio
import gzip
import json
import random
import time
from pathlib import Path

import aiohttp

SAMPLES = Path(__file__).parent.parent / "src/beacon/telemetry-schema/samples/v1"
rng = random.Random(1)


def bodies(count: int, events: int) -> list[bytes]:
    meta = json.loads((SAMPLES / "upload-metadata/valid/full.json").read_text())
    meta.update(app_package="com.example.load", sent_at=int(time.time() * 1000))
    valid = [json.loads(p.read_text()) for p in sorted((SAMPLES / "event/valid").glob("*.json"))]
    result = []
    for _ in range(count):
        lines = [meta]
        for _ in range(events):
            event = dict(rng.choice(valid))
            event["call_id"] = f"{rng.getrandbits(64):016x}"
            event["start_time"] = meta["sent_at"] - rng.randint(1_000, 60_000)
            lines.append(event)
        result.append(gzip.compress(("\n".join(json.dumps(x) for x in lines) + "\n").encode()))
    return result


async def step(url: str, payloads: list[bytes], senders: int, seconds: float) -> dict[str, object]:
    latencies: list[float] = []
    codes: dict[int, int] = {}
    deadline = time.monotonic() + seconds
    headers = {"Content-Type": "application/x-ndjson", "Content-Encoding": "gzip"}

    async def sender(session: aiohttp.ClientSession) -> None:
        while time.monotonic() < deadline:
            started = time.monotonic()
            try:
                async with session.post(url, data=rng.choice(payloads), headers=headers) as r:
                    await r.read()
                    code = r.status
            except aiohttp.ClientError:
                code = 0
            latencies.append(time.monotonic() - started)
            codes[code] = codes.get(code, 0) + 1

    connector = aiohttp.TCPConnector(limit=senders, ssl=False)
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        began = time.monotonic()
        await asyncio.gather(*(sender(session) for _ in range(senders)))
        elapsed = time.monotonic() - began
    latencies.sort()

    def quantile(q: float) -> float:
        return latencies[min(len(latencies) - 1, int(q * len(latencies)))] * 1000

    return {
        "senders": senders,
        "uploads/s": round(codes.get(200, 0) / elapsed, 1),
        "p50 ms": round(quantile(0.5)),
        "p95 ms": round(quantile(0.95)),
        "p99 ms": round(quantile(0.99)),
        "codes": dict(sorted(codes.items())),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--levels", default="4,16,64")
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--events", type=int, default=40)
    args = parser.parse_args()
    payloads = bodies(200, args.events)
    print(f"body {sum(map(len, payloads)) // len(payloads)} bytes gzip, {args.events} events")
    for senders in map(int, args.levels.split(",")):
        result = await step(args.url, payloads, senders, args.seconds)
        events_per_s = result["uploads/s"] * args.events
        print(json.dumps({**result, "events/s": round(events_per_s)}))


if __name__ == "__main__":
    asyncio.run(main())
