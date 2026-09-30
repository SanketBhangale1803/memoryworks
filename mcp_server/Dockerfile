FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
ENV ORGMEMORY_API_URL=http://backend:8000
EXPOSE 8001
CMD ["python", "server.py", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8001"]
