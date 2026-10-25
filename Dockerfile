FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY jobscout jobscout
COPY config.toml .

RUN useradd -m app && mkdir /data && chown app /data
USER app
ENV STATE_PATH=/data/state.json
VOLUME /data

CMD ["python", "-m", "jobscout", "--loop", "30"]
