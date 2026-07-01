FROM python:3.10-slim
WORKDIR /app

# 1. SYSTEM DEPENDENCIES (Required for C++ compilation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 2. INSTALL ONLY THE ENGINE AND BASE DEPENDENCIES FIRST
RUN pip install --no-cache-dir pandas numpy cmdstanpy==1.2.0

# 3. DOWNLOAD AND COMPILE THE ENGINE IN A FIXED ABSOLUTE PATH
RUN install_cmdstan -d /opt/cmdstan --version 2.33.1

# 4. DECLARE THE ENVIRONMENT VARIABLE (So Prophet knows where to look)
ENV CMDSTAN=/opt/cmdstan/cmdstan-2.33.1

# 5. NOW INSTALL PROPHET (It will find CMDSTAN and hook into the engine)
RUN pip install --no-cache-dir prophet==1.1.5

# 6. INSTALL THE REST OF THE PROJECT
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000
CMD ["uvicorn", "inference.api:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]