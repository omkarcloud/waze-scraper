FROM chetan1111/botasaurus:chrome-151

ENV PYTHONUNBUFFERED=1

COPY requirements.txt .

RUN python -m pip install --no-cache-dir -r requirements.txt

RUN mkdir app
WORKDIR /app
COPY . /app

EXPOSE 8000

# Real Google Chrome + Xvfb come with the base image: a headed Chrome earns
# Waze's reCAPTCHA token (browser_client.py); every API call to Waze is
# plain HTTP with that token.
CMD ["python", "run.py"]
