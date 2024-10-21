FROM sdwan-docker-dev-virtual.usw1.packages.broadcom.com/library/python:3.10-alpine

COPY requirements.txt requirements-dev.txt /tmp/

RUN echo >/etc/pip.conf "[global]" && \
    echo >>/etc/pip.conf "index-url = https://artifactory.eng.vmware.com/artifactory/api/pypi/sebu-edgeops-pypi-local/simple" && \
    echo >>/etc/pip.conf "extra-index-url = https://artifactory.eng.vmware.com/artifactory/api/pypi/pypi-virtual/simple"

RUN adduser --uid 501 --disabled-password svc svc && \
    apk add --no-cache bash git rsync openssh-client && \
    pip3 install --no-cache-dir -r /tmp/requirements-dev.txt

CMD ["jenkins-jobs", "--help"]
