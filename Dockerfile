FROM python:3.8-slim

RUN pip install flask requests

COPY ./app /app

WORKDIR /app

ENV PYTHONPATH=/app

EXPOSE 9002

CMD ["python /app/main.py"]



