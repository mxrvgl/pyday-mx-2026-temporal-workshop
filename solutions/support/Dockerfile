# The support service: the API, and (once you write it) the worker. Same image, different command.
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Download Temporal's test server now, so `make check` works offline.
RUN python <<'EOF'
import asyncio
from temporalio.testing import WorkflowEnvironment

async def download():
    env = await WorkflowEnvironment.start_time_skipping()
    await env.shutdown()

asyncio.run(download())
EOF
COPY . .
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
