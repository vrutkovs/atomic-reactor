FROM fedora:latest
# e2fsprogs -- docker @ F20 wants it
RUN yum -y install docker git python-docker-py python-setuptools e2fsprogs koji python-backports-lzma osbs gssproxy python-pygraphviz python-pycallgraph
ADD ./atomic-reactor.tar.gz /tmp/
# Fedora 23 has old pycallgraph 0.5
RUN cd /tmp/atomic-reactor-*/ && python setup.py install
CMD ["atomic-reactor-pycallgraph", "--verbose", "inside-build"]
