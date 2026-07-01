FROM python:3.10-slim
WORKDIR /app


RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*


    
RUN pip install --no-cache-dir pandas numpy cmdstanpy==1.2.0
RUN install_cmdstan -d /opt/cmdstan --version 2.33.1
ENV CMDSTAN=/opt/cmdstan/cmdstan-2.33.1



RUN pip install --no-cache-dir prophet==1.1.5


COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt