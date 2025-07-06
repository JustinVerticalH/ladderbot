FROM python:3.12
WORKDIR /docker
COPY . .
RUN pip install -r requirements.txt
CMD python ladderbot.py