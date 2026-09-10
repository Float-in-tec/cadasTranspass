# ── Imagem base slim para manter o container leve ──────────────────────────
FROM python:3.11-slim

# Evita escrita de .pyc e garante logs em tempo real no Render
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copia apenas o manifesto de dependências primeiro (melhor cache de layers)
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Copia o restante do código
COPY . .

# Usuário não-root por segurança
RUN adduser --disabled-password --gecos "" appuser \
 && chown -R appuser /app
USER appuser

# Render injeta a PORT via variável de ambiente; padrão local: 8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
