# Workshop tooling: the CLI behind `make buy`, `make board`... Not part of the shop.
FROM python:3.13-slim
RUN pip install --no-cache-dir "httpx==0.28.1" "typer==0.27.2" "rich==15.0.0"
WORKDIR /scripts
COPY cli.py board.py ./
ENTRYPOINT ["python", "cli.py"]
