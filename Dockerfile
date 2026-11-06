# Базовый образ закреплён до точной версии Python и выпуска Debian
# (trixie = Debian 13). Просто «python:3.12-slim» однажды молча переехал
# бы на другую версию — и сайт пересобрался бы не на том, что проверено.
# Обновлять раз в месяц-два, с прогоном проверок.
# Пересобирать с --pull, иначе docker возьмёт старую копию образа из кэша
# и заплатки Debian так и не приедут:  docker compose build --pull
FROM python:3.12.16-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# --only-binary=:all: — ставить только готовые сборки (wheel). Исходник
# (sdist) при установке выполняет свой setup.py, то есть чужой код прямо
# во время сборки; к тому же в slim-образе нет компилятора. Для всех
# закреплённых версий готовые сборки есть (x86_64 и arm64 — Raspberry Pi).
# -c constraints.txt — точные версии всех вложенных библиотек.
COPY api/requirements.txt api/constraints.txt ./
RUN pip install --only-binary=:all: -r requirements.txt -c constraints.txt

# Библиотеки своих источников (MODE=private) — отдельным слоем. В
# публичной версии файл пустой (одни комментарии): источников нет.
#
# Слой отдельный, чтобы правка одного файла не пересобирала другой.
# Проверка grep нужна для файла, где остались одни комментарии (как в
# публичной версии): тогда ставить нечего, и шаг просто пропускается,
# а не роняет сборку на старом pip, который на пустой список ругается.
COPY api/requirements-private.txt .
RUN if grep -Eq '^[[:space:]]*[^#[:space:]]' requirements-private.txt; then \
        pip install --only-binary=:all: -r requirements-private.txt -c constraints.txt; \
    else \
        echo "requirements-private.txt пуст — ставить нечего"; \
    fi

# Отдельный пользователь без прав администратора, номер закреплён (1000 —
# тот же, что выдавался раньше автоматически): файлы базы в томе
# принадлежат ему, и другой номер после пересборки оставил бы сайт без
# доступа к собственной базе.
#
# Код (/app) остаётся во владении root и только для чтения: найдя дыру,
# злоумышленник не сможет подменить код сайта. Писать можно только в /data.
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /nonexistent \
               --shell /usr/sbin/nologin app \
    && mkdir -p /data && chown app:app /data && chmod 700 /data

COPY api ./api
COPY web ./web

USER app

# HOME=/tmp: файловая система контейнера только для чтения (read_only в
# docker-compose.yml), а /tmp — единственное место для временных файлов.
ENV DB_PATH=/data/anime.db WEB_DIR=/app/web PORT=8000 HOME=/tmp
EXPOSE 8000

# Docker сам раз в 30 секунд спрашивает сайт «жив?». Ответ виден в
# docker compose ps (healthy / unhealthy).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"]

# Один рабочий процесс — это не «не дошли руки», а условие работы: гостевые
# пропуска и счётчики частоты живут в памяти процесса. Со вторым процессом
# гость случайно оказывался бы то впущен, то нет, а лимиты делились бы
# надвое. Для домашнего сайта одного процесса хватает с большим запасом.
#
# --no-proxy-headers: адрес посетителя разбирает только приложение
# (client_ip в api/main.py, по X-Real-IP от nginx). У uvicorn разбор
# X-Forwarded-For включён ПО УМОЛЧАНИЮ (для запросов с 127.0.0.1) —
# раньше его просто не указывали, и он оставался включённым. Два разбора
# одного заголовка — лишний способ ошибиться, оставлен один.
# --no-server-header: не сообщать наружу «server: uvicorn».
# --timeout-graceful-shutdown 10: при остановке ждать открытые запросы
# (например, поток видео) не дольше 10 секунд — docker даёт 20
# (stop_grace_period), и сайт успевает закрыться аккуратно.
#
# exec — чтобы uvicorn получал сигнал остановки сам, а не ждал, пока его
# добьют по таймауту.
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-proxy-headers --no-server-header --timeout-graceful-shutdown 10"]
