# Scalable RAG Queue

An asynchronous, horizontally scalable retrieval-augmented generation (RAG) API built with FastAPI, RQ, Valkey/Redis, Qdrant, and Docker. The API enqueues questions for background processing and returns a job ID that clients can poll for status and results. RQ workers retrieve relevant passages from an indexed PDF and use them as context for model-generated responses.

The queue-based architecture separates API request handling from slower RAG inference work. Worker capacity can be scaled horizontally by running additional RQ worker processes against the same queue, allowing multiple jobs to be processed without blocking the API server.

The model layer is provider-flexible. This implementation uses Gemma 2B and `embeddinggemma` through Ollama because it was developed without hosted API credentials. Ollama exposes an OpenAI-compatible endpoint, so it can be replaced with a hosted provider without changing the FastAPI, RQ, Redis, retrieval, or job-status flow.


## Architecture

Document ingestion happens separately from query processing. `index.py` reads `tmp/pdfs/nodejs.pdf`, divides it into overlapping chunks, creates vector embeddings with `embeddinggemma`, and stores the chunks and embeddings in the Qdrant collection `learning_rag_local`.

When a client sends a question through the `query` parameter of `POST /chat`, FastAPI places a `process_query` job on the RQ queue in Valkey/Redis and immediately returns a job ID. This keeps the API responsive instead of making the client wait for retrieval and model inference to finish.

An available RQ worker takes the job from the queue, embeds the question, and searches Qdrant for the most relevant document chunks. The worker adds those chunks to the model's system prompt and sends the grounded prompt to Gemma 2B through Ollama's OpenAI-compatible endpoint.

After generation finishes, RQ stores the answer and job state in Redis. The client calls `GET /job-status` with its job ID to check whether the job is queued, running, finished, or failed and to retrieve the completed answer.

Additional workers can connect to the same queue to process jobs concurrently. This scales background-processing capacity independently of the FastAPI server, while the model implementation can be changed from Ollama to a hosted provider without altering the queue workflow.

## Prerequisites

- Python 3.11 or newer
- Docker Desktop (for Valkey and Qdrant)
- [Ollama](https://ollama.com/) running locally

Pull the required Ollama models:

```powershell
ollama pull gemma:2b
ollama pull embeddinggemma
```

## Setup

Clone the repository, create a virtual environment, and install dependencies:

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Start Valkey and Qdrant:

```powershell
docker compose up -d
```

Create the local document directory and place the PDF at `tmp/pdfs/nodejs.pdf`:

```powershell
New-Item -ItemType Directory -Force tmp\pdfs
```

The `tmp/` directory is intentionally ignored by Git so local or copyrighted source documents are not committed.

Index the PDF once (and again whenever it changes):

```powershell
python index.py
```

## Run the application

Use separate terminals, with the virtual environment activated in each.

Start the API:

```powershell
python -m main
```

Start an RQ worker on Windows:

```powershell
rq worker --worker-class rq.worker.SimpleWorker
```

RQ's default worker relies on Unix process APIs such as `fork()`, which are unavailable on Windows. `SimpleWorker` runs jobs in the worker process and avoids that limitation. To process more than one job concurrently, start the same command in additional terminals. Each worker handles one job at a time, while all workers may query the same Ollama server.

Open the interactive API documentation at <http://localhost:8000/docs>.

## API workflow

Queue a question:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/chat?query=What%20are%20Promises%3F"
```

The response contains a job ID:

```json
{"status":"queued","job_id":"..."}
```

Poll for the result:

```powershell
Invoke-RestMethod "http://localhost:8000/job-status?job_id=YOUR_JOB_ID"
```

Possible states are `queued`, `started`, `finished`, and `failed`. RQ currently retains completed results for its default 500-second period.

## Benchmarking

`benchmark.py` submits concurrent questions, polls each job to completion, and records enqueue latency, queue wait time, worker processing time, end-to-end latency, throughput, and success rate. Start the API and workers first, then run:

```powershell
python benchmark.py --requests 10 --concurrency 2 --workers 2
```

The worker count is recorded as benchmark metadata; the script does not start workers itself. Full per-job results are written to `benchmark-results.json`, which is ignored by Git.

For a meaningful scaling comparison, run the same query and request count first with one worker and then with two workers:

```powershell
python benchmark.py --requests 10 --concurrency 2 --workers 1 --output benchmark-results-1-worker.json
python benchmark.py --requests 10 --concurrency 2 --workers 2 --output benchmark-results-2-workers.json
```

## Switching to hosted APIs

The queue architecture is provider-independent. To use a hosted service, replace the model client in `queues/worker.py` and, if desired, the embedding model in both `queues/worker.py` and `index.py`.

For example, an OpenAI-compatible hosted provider generally requires changing:

- `base_url` to the provider endpoint
- `api_key` to an environment variable such as `OPENAI_API_KEY`
- `model` to the provider's chat model
- the embedding implementation and model used during both indexing and retrieval

Do not mix embedding models for the same Qdrant collection. If the embedding provider or model changes, recreate and re-index the collection so stored document vectors and query vectors use the same embedding space.

Only the inference and embedding adapters need to change; request queuing, worker scaling, retrieval, result polling, and the public API can remain intact.

## Project structure

```text
client/rq_client.py   Redis connection and RQ queue
queues/worker.py      Retrieval and local-model generation job
index.py              PDF ingestion and Qdrant indexing
server.py             FastAPI routes
main.py               Uvicorn entry point
docker-compose.yml    Valkey and Qdrant services
```

## Notes

- Ollama is expected at `http://localhost:11434`.
- Valkey/Redis is expected at `localhost:6379`.
- Qdrant is expected at `http://localhost:6333`.
- More workers can improve queue throughput, but concurrent inference increases RAM/VRAM use and may be serialized internally by Ollama.
