FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# If certs/vault-ca.pem exists, build a combined CA bundle (system defaults + custom CA).
# If it doesn't exist, just use the system defaults.
COPY certs/ /app/certs/
RUN if [ -f /app/certs/vault-ca.pem ]; then \
      cat $(python -c "import certifi; print(certifi.where())") \
          /app/certs/vault-ca.pem > /app/certs/ca-bundle.pem; \
    else \
      cp $(python -c "import certifi; print(certifi.where())") /app/certs/ca-bundle.pem; \
    fi

ENV REQUESTS_CA_BUNDLE=/app/certs/ca-bundle.pem
ENV SSL_CERT_FILE=/app/certs/ca-bundle.pem

COPY sensitive_patterns.json server.py ./
COPY gateway/ /app/gateway/

RUN mkdir -p data

# Default port — actual port is set in config.json (mounted as a volume)
EXPOSE 18795

CMD ["python", "server.py"]
