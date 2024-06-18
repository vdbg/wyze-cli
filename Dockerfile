FROM python:3.11-alpine

# Comment out the next 2 lines if using regular wyze-sdk in requirements.txt
RUN apk update
RUN apk add git gcc musl-dev

RUN addgroup -S wyze && adduser -S wyze -G wyze

USER wyze

WORKDIR /app

# Prevents Python from writing pyc files to disc
ENV PYTHONDONTWRITEBYTECODE 1
# Prevents Python from buffering stdout and stderr
ENV PYTHONUNBUFFERED 1
# Install location of upgraded pip
ENV PATH /home/wyze/.local/bin:$PATH

COPY requirements.txt     /app/

RUN pip install --no-cache-dir --disable-pip-version-check --upgrade pip
RUN pip install --no-cache-dir -r ./requirements.txt

COPY app.py                 /app/

ENTRYPOINT [ "python", "app.py" ]
CMD [ "--help" ]

