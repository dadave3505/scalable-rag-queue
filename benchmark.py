import argparse
import asyncio
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


def raise_api_error(response: httpx.Response):
    if response.is_error:
        raise RuntimeError(
            f"API returned {response.status_code} for "
            f"{response.request.method} {response.request.url}: {response.text}"
        )


def percentile(values: list[float], percentage: float):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil((percentage / 100) * len(ordered)) - 1)
    return round(ordered[index], 3)


def summarize(values: list[float]):
    if not values:
        return {"min": None, "p50": None, "p95": None, "max": None}
    return {
        "min": round(min(values), 3),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "max": round(max(values), 3),
    }


async def run_request(
    client: httpx.AsyncClient,
    base_url: str,
    query: str,
    poll_interval: float,
    timeout: float,
):
    request_started = time.perf_counter()
    response = await client.post(f"{base_url}/chat", params={"query": query})
    raise_api_error(response)
    enqueue_seconds = time.perf_counter() - request_started
    job_id = response.json()["job_id"]

    while True:
        if time.perf_counter() - request_started > timeout:
            return {
                "job_id": job_id,
                "status": "timeout",
                "enqueue_seconds": enqueue_seconds,
                "end_to_end_seconds": time.perf_counter() - request_started,
            }

        status_response = await client.get(
            f"{base_url}/job-status", params={"job_id": job_id}
        )
        raise_api_error(status_response)
        payload = status_response.json()

        if payload["status"] in {"finished", "failed"}:
            return {
                "job_id": job_id,
                "status": payload["status"],
                "enqueue_seconds": enqueue_seconds,
                "end_to_end_seconds": time.perf_counter() - request_started,
                **payload.get("metrics", {}),
            }

        await asyncio.sleep(poll_interval)


async def benchmark(args):
    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    semaphore = asyncio.Semaphore(args.concurrency)

    async with httpx.AsyncClient(limits=limits) as client:
        async def bounded_request():
            async with semaphore:
                return await run_request(
                    client,
                    args.base_url.rstrip("/"),
                    args.query,
                    args.poll_interval,
                    args.timeout,
                )

        run_started = time.perf_counter()
        results = await asyncio.gather(
            *(bounded_request() for _ in range(args.requests))
        )
        run_seconds = time.perf_counter() - run_started

    completed = [result for result in results if result["status"] == "finished"]
    failed = [result for result in results if result["status"] != "finished"]

    report = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "base_url": args.base_url,
            "requests": args.requests,
            "concurrency": args.concurrency,
            "worker_count": args.workers,
            "query": args.query,
        },
        "summary": {
            "completed": len(completed),
            "failed_or_timed_out": len(failed),
            "success_rate_percent": round(100 * len(completed) / len(results), 2),
            "benchmark_seconds": round(run_seconds, 3),
            "throughput_jobs_per_minute": round(60 * len(completed) / run_seconds, 2),
            "enqueue_latency_seconds": summarize(
                [result["enqueue_seconds"] for result in results]
            ),
            "end_to_end_latency_seconds": summarize(
                [result["end_to_end_seconds"] for result in completed]
            ),
            "queue_wait_seconds": summarize(
                [result["queue_wait_seconds"] for result in completed
                 if result.get("queue_wait_seconds") is not None]
            ),
            "worker_processing_seconds": summarize(
                [result["processing_seconds"] for result in completed
                 if result.get("processing_seconds") is not None]
            ),
        },
        "jobs": results,
    }

    output_path = Path(args.output)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"\nFull results written to {output_path}")


def parse_args():
    parser = argparse.ArgumentParser(description="Benchmark the queued RAG API")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--requests", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Worker count to record in the report (workers are started separately)",
    )
    parser.add_argument("--query", default="Explain how to use multer for file uploading?")
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", default="benchmark-results.json")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(benchmark(parse_args()))
