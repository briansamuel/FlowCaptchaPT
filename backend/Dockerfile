FROM python:3.11-slim

WORKDIR /app

# Install Chrome + Xvfb + dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg2 curl unzip procps \
    # Xvfb for virtual display
    xvfb \
    # Chrome dependencies
    libnss3 libatk-bridge2.0-0 libdrm2 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libasound2 libpangocairo-1.0-0 \
    libgtk-3-0 libxshmfence1 fonts-liberation libx11-xcb1 \
    libxcb1 libxext6 libxfixes3 libxi6 libxrender1 libxtst6 \
    && rm -rf /var/lib/apt/lists/*

# Install Google Chrome stable
RUN wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update && apt-get install -y /tmp/chrome.deb \
    && rm /tmp/chrome.deb && rm -rf /var/lib/apt/lists/*

# Python deps
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend app
COPY backend/app/ ./app/
COPY backend/entrypoint.sh .
RUN chmod +x entrypoint.sh

# Frontend static files (optional - uncomment when frontend/ dir exists)
# COPY frontend/ ./frontend/

RUN mkdir -p /app/data /app/chrome-profile

EXPOSE 9339

ENTRYPOINT ["./entrypoint.sh"]
