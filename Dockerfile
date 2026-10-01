FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api ./api
COPY web ./web

# Отдельный пользователь без прав администратора.
# Если в приложении найдётся дыра, злоумышленник окажется обычным
# пользователем внутри контейнера, а не хозяином системы.
RUN useradd --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data && chown -R app:app /app /data
USER app

ENV DB_PATH=/data/anime.db WEB_DIR=/app/web PORT=8000
EXPOSE 8000

# Один рабочий процесс — это не «не дошли руки», а условие работы: гостевые
# пропуска и счётчики частоты живут в памяти процесса. Со вторым процессом
# гость случайно оказывался бы то впущен, то нет, а лимиты делились бы
# надвое. Для домашнего сайта одного процесса хватает с большим запасом.
#
# --proxy-headers и --forwarded-allow-ips='*' убраны намеренно. С ними
# uvicorn сам переписывал адрес клиента из X-Forwarded-For, доверяя этому
# заголовку от КОГО УГОДНО, и в request.client оказывалось то, что прислал
# сам посетитель. Адрес разбирает приложение (client_ip в api/main.py):
# оно берёт X-Real-IP, который nginx перезаписывает начисто, и проверяет,
# что это вообще адрес. Два разных разбора одного заголовка — лишний способ
# ошибиться; оставлен один, тот, что под нашим контролем.
#
# exec — чтобы uvicorn стал первым процессом и получал сигнал остановки
# сам, а не ждал, пока его добьют по таймауту.
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
