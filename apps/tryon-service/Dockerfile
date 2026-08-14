FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip && rm -rf /var/lib/apt/lists/*
COPY apps/tryon-service/requirements-gpu.txt apps/tryon-service/requirements.txt ./
RUN python3 -m pip install --no-cache-dir -r requirements-gpu.txt
COPY apps/tryon-service /app/apps/tryon-service
ENV PYTHONUNBUFFERED=1 ENABLE_RESEARCH_3D=false TRYON_DEVICE=cuda:0 TRYON_RESULT_DIR=/tmp/patternmate-tryon
EXPOSE 8790
CMD ["python3", "-m", "uvicorn", "app:app", "--app-dir", "/app/apps/tryon-service", "--host", "0.0.0.0", "--port", "8790"]

