# My Objaverse HTTP API

This API turns my Objaverse-XL data access into HTTP endpoints. I keep the repository data-focused, and I expose metadata lookup plus asynchronous download jobs through FastAPI.

## Files I added

| File | Purpose |
| --- | --- |
| `objaverse/api.py` | My FastAPI application with health, source, annotation, summary, download-job, and job-status endpoints. |
| `objaverse/xl/thingiverse.py` | My Thingiverse downloader module so `objaverse.xl` imports cleanly and the API can use all advertised XL sources. |
| `requirements-api.txt` | My API-focused install file that includes the base package requirements plus FastAPI and Uvicorn. |
| `Dockerfile` | My production container image for running the API with Uvicorn. |
| `apprunner.yaml` | My AWS App Runner source deployment configuration. |
| `tests/test_api_server.py` | My tests for the API routes and background download job flow. |
| `docs/api.md` | My deployment and usage guide. |

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-api.txt
pip install -e .
uvicorn objaverse.api:app --host 0.0.0.0 --port 8000 --reload
```

Open:

```text
http://localhost:8000/docs
```

## Endpoints

### `GET /health`

I use this for load balancer and container health checks.

```bash
curl http://localhost:8000/health
```

### `GET /v1/sources`

I list the XL source adapters that this API can use.

```bash
curl http://localhost:8000/v1/sources
```

### `GET /v1/annotations`

I page through Objaverse-XL metadata.

```bash
curl "http://localhost:8000/v1/annotations?source=github&file_type=glb&limit=10"
```

Useful query parameters:

| Parameter | Description |
| --- | --- |
| `source` | `github`, `thingiverse`, `smithsonian`, or `sketchfab`. |
| `file_type` | A 3D file extension/type such as `glb`, `obj`, or `fbx`. |
| `license` | Exact license string from the dataset metadata. |
| `limit` | Page size from 1 to 1000. |
| `offset` | Zero-based row offset. |
| `download_dir` | Where I cache metadata; local paths and fsspec URLs are supported by the underlying package. |
| `refresh` | Set to `true` when I want to re-fetch metadata. |

### `GET /v1/annotations/summary`

I return total object count, source counts, and common file types.

```bash
curl http://localhost:8000/v1/annotations/summary
```

### `POST /v1/downloads`

I create a background download job. For production, I should keep this for small-to-medium jobs and move heavy work to AWS Batch or ECS workers.

```bash
curl -X POST http://localhost:8000/v1/downloads \
  -H "Content-Type: application/json" \
  -d '{
    "source": "github",
    "file_type": "glb",
    "limit": 5,
    "download_dir": "~/.objaverse",
    "processes": 2
  }'
```

### `GET /v1/jobs/{job_id}`

I check whether a download job is queued, running, succeeded, or failed.

```bash
curl http://localhost:8000/v1/jobs/YOUR_JOB_ID
```

## Run with Docker

```bash
docker build -t my-objaverse-api .
docker run --rm -p 8000:8000 my-objaverse-api
```

## AWS deployment plan

### Simple web API path: AWS App Runner

I use App Runner when I want the quickest managed HTTPS web API:

1. Create or choose an Amazon ECR repository.
2. Build and push the Docker image:

   ```bash
   aws ecr create-repository --repository-name my-objaverse-api
   aws ecr get-login-password --region YOUR_REGION \
     | docker login --username AWS --password-stdin YOUR_ACCOUNT_ID.dkr.ecr.YOUR_REGION.amazonaws.com
   docker build -t my-objaverse-api .
   docker tag my-objaverse-api:latest YOUR_ACCOUNT_ID.dkr.ecr.YOUR_REGION.amazonaws.com/my-objaverse-api:latest
   docker push YOUR_ACCOUNT_ID.dkr.ecr.YOUR_REGION.amazonaws.com/my-objaverse-api:latest
   ```

3. Create an App Runner service from the ECR image.
4. Set the service port to `8000`.
5. Set the health check path to `/health`.
6. Attach an instance role with S3 permissions if I use `s3://...` for `download_dir`.
7. Add environment variables if I want to override host or port:

   ```text
   OBJAVERSE_API_HOST=0.0.0.0
   OBJAVERSE_API_PORT=8000
   ```

AWS App Runner is designed to deploy source code or container images directly to a scalable managed web service, so it is the cleanest choice for this API when I do not need GPU rendering in the web process.

### More control path: ECS Fargate plus Application Load Balancer

I use ECS Fargate when I want VPC control, private subnets, custom scaling, or a broader production setup:

1. Push the same Docker image to ECR.
2. Create an ECS cluster.
3. Create a Fargate task definition with container port `8000`.
4. Add a task role with S3 read/write access if I use S3 paths.
5. Create an Application Load Balancer and target group that forwards to port `8000`.
6. Create an ECS service attached to that target group.
7. Configure the target group health check path as `/health`.
8. Add HTTPS with AWS Certificate Manager on the load balancer listener.

Amazon ECS services on Fargate support Application Load Balancers for HTTP/HTTPS traffic, which fits this API well.

### Heavy download and render path: AWS Batch

I use AWS Batch when I want large downloads or Blender rendering jobs:

1. Store requested job parameters in S3 or a queue.
2. Submit AWS Batch jobs from this API instead of doing heavy work in the web container.
3. Use a Batch compute environment with CPU instances for downloads.
4. Use GPU-enabled Batch compute resources for Blender rendering.
5. Store downloaded objects and render outputs in S3.
6. Return S3 object keys or pre-signed URLs from a separate job-status endpoint.

AWS Batch supports container jobs with vCPU, memory, IAM role, environment, and GPU resource requirements, so it is the right place for long-running dataset or rendering work.

## IAM permissions I need on AWS

If I store objects in S3, the runtime role needs access to my bucket. Start narrow and grant only the prefixes this API uses:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::MY_BUCKET",
        "arn:aws:s3:::MY_BUCKET/objaverse/*"
      ]
    }
  ]
}
```

## Production notes

- I should keep `/v1/downloads` for bounded jobs, not full-dataset downloads from a public HTTP request.
- I should add authentication before exposing this publicly, such as an API Gateway, ALB authentication, Cognito, or a private VPC-only service.
- I should store jobs in DynamoDB or a database for production because the current in-memory job store resets when the container restarts.
- I should add SQS plus AWS Batch or ECS workers before running large download or Blender workloads.
- I should review object-level licenses before redistributing downloaded assets or rendered outputs.
