FROM python:3.12-slim

# Évite les fichiers .pyc et les logs bufferisés (important en container)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Couche de dépendances séparée — reconstruite seulement si requirements.txt change
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code source
COPY app/       ./app/
COPY mock_tms/  ./mock_tms/

EXPOSE 8000

# Commande par défaut : l'API REST
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
