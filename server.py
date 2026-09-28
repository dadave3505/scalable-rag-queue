from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query, status
from client.rq_client import queue
from queues.worker import process_query



app = FastAPI()


def _elapsed_seconds(start, end):
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 3)


def _job_metrics(job):
    enqueued_at = getattr(job, "enqueued_at", None)
    started_at = getattr(job, "started_at", None)
    ended_at = getattr(job, "ended_at", None)

    return {
        "queue_wait_seconds": _elapsed_seconds(enqueued_at, started_at),
        "processing_seconds": _elapsed_seconds(started_at, ended_at),
        "total_job_seconds": _elapsed_seconds(enqueued_at, ended_at),
    }


@app.get('/')
def root():
    return {"status": 'Server is up and running'}

@app.post('/chat', status_code=status.HTTP_202_ACCEPTED)
def chat(
    query: str = Query(..., min_length=1, description="The user's question")
):
    job = queue.enqueue(process_query, query)

    return { "status": "queued", "job_id": job.id }

@app.get('/job-status')
def get_result(
        job_id: str = Query(..., description="Job ID")
):
    job = queue.fetch_job(job_id=job_id)

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found or its result has expired.",
        )

    if job.is_finished:
        return {
            "status": "finished",
            "result": job.return_value(),
            "metrics": _job_metrics(job),
        }
    if job.is_failed:
        return {"status": "failed", "result": None, "metrics": _job_metrics(job)}
    if job.is_started:
        return {"status": "started", "result": None}
    return {"status": "queued", "result": None}
