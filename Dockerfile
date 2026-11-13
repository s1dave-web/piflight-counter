FROM python:3.13-slim
LABEL org.opencontainers.image.source="https://github.com/s1dave-web/piflight-counter" \
      org.opencontainers.image.description="Counts piFlight LED displays online: stores salted ID hashes, no IP addresses"
RUN useradd --system --uid 10001 counter && mkdir /data && chown counter /data
COPY counter.py /app/counter.py
USER counter
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"
CMD ["python3", "-u", "/app/counter.py"]
