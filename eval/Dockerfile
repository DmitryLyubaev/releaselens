# Matches the interpreter the harness is developed and tested against. Shipping 3.12
# while testing on 3.14 would mean the sweep that produces the README's numbers runs
# on a runtime no test ever exercised.
FROM python:3.14-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY golden/ golden/

EXPOSE 8000
CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
