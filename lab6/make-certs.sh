#!/usr/bin/env bash

set -euo pipefail

mkdir -p certs

if [ -e certs/ca.key ] || [ -e certs/server.key ]; then
    echo "Certificates already exist; refusing to replace them." >&2
    exit 1
fi

openssl req \
  -x509 \
  -newkey rsa:2048 \
  -nodes \
  -days 365 \
  -keyout certs/ca.key \
  -out certs/ca.crt \
  -subj "/CN=Lab6 Local CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign"

openssl req \
  -new \
  -newkey rsa:2048 \
  -nodes \
  -keyout certs/server.key \
  -out certs/server.csr \
  -subj "/CN=gitlab.lab.test"

cat > certs/server.ext <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:gitlab.lab.test,DNS:registry.lab.test,DNS:sso.lab.test,DNS:grafana.lab.test
EOF

openssl x509 \
  -req \
  -in certs/server.csr \
  -CA certs/ca.crt \
  -CAkey certs/ca.key \
  -CAcreateserial \
  -out certs/server.crt \
  -days 365 \
  -sha256 \
  -extfile certs/server.ext

chmod 600 certs/ca.key certs/server.key
chmod 644 certs/ca.crt certs/server.crt