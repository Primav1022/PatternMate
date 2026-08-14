FROM python:3.12-slim
WORKDIR /app
COPY apps/geometry-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY _handoff_pack /app/_handoff_pack
COPY data/ir/v1_rule_ready /app/data/ir/v1_rule_ready
COPY data/seed/dxf/v1_annotated /app/data/seed/dxf/v1_annotated
COPY apps/geometry-service /app/apps/geometry-service
ENV PYTHONUNBUFFERED=1
EXPOSE 8788
CMD ["uvicorn", "apps.geometry-service.app:app", "--host", "0.0.0.0", "--port", "8788"]
