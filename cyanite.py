import itertools
import time
import pylru

try:
    from graphite_api.intervals import Interval, IntervalSet
    from graphite_api.node import LeafNode, BranchNode
except ImportError:
    from graphite.intervals import Interval, IntervalSet
    from graphite.node import LeafNode, BranchNode

import requests


def chunk(nodelist, length):
    chunklist = []
    linelength = 0
    for node in nodelist:
        # the magic number 6 is because the nodes list gets padded
        # with '&path=' in the resulting request
        nodelength = len(str(node)) + 6

        if linelength + nodelength > length:
            yield chunklist
            chunklist = [node]
            linelength = nodelength
        else:
            chunklist.append(node)
            linelength += nodelength
    yield chunklist


class CyaniteLeafNode(LeafNode):
    __fetch_multi__ = 'cyanite'


class URLs(object):
    def __init__(self, hosts):
        self.iterator = itertools.cycle(hosts)

    @property
    def host(self):
        return next(self.iterator)

    @property
    def paths(self):
        return '{0}/paths'.format(self.host)

    @property
    def metrics(self):
        return '{0}/metrics'.format(self.host)
urls = None
urllength = 8000
leafcache = pylru.lrucache(10000)
find_timeout = 3
fetch_timeout = 10

class CyaniteReader(object):
    __slots__ = ('path',)

    def __init__(self, path):
        self.path = path

    def fetch(self, start_time, end_time):
        data = requests.get(urls.metrics, params={'path': self.path,
                                                  'from': start_time,
                                                  'to': end_time}).json()
        if 'error' in data:
            return (start_time, end_time, end_time - start_time), []
        if len(data['series']) == 0:
            return
        time_info = data['from'], data['to'], data['step']
        return time_info, data['series'].get(self.path, [])

    def get_intervals(self):
        # TODO use cyanite info
        return IntervalSet([Interval(0, int(time.time()))])


class CyaniteFinder(object):
    __fetch_multi__ = 'cyanite'

    def __init__(self, config=None):
        global urls
        global urllength
        global find_timeout
        global fetch_timeout
        if config is not None:
            if 'urls' in config['cyanite']:
                urls = config['cyanite']['urls']
            else:
                urls = [config['cyanite']['url'].strip('/')]
            if 'urllength' in config['cyanite']:
                urllength = config['cyanite']['urllength']
            if 'find_timeout' in config['cyanite']:
                find_timeout = config['cyanite']['find_timeout']
            if 'fetch_timeout' in config['cyanite']:
                fetch_timeout = config['cyanite']['fetch_timeout']
        else:
            from django.conf import settings
            urls = getattr(settings, 'CYANITE_URLS')
            if not urls:
                urls = [settings.CYANITE_URL]
            urllength = getattr(settings, 'CYANITE_URL_LENGTH', urllength)
        urls = URLs(urls)

    def find_nodes(self, query):
        leafpath = leafcache.get(query.pattern);
        if leafpath:
            yield CyaniteLeafNode(query.pattern, CyaniteReader(query.pattern))
        else:
            paths = requests.get(urls.paths,
                             params={'query': query.pattern, 'from': query.startTime, 'to': query.endTime}, timeout=find_timeout).json()
            for path in paths:
                if path['leaf']:
                    leafcache[path['path']] = True
                    yield CyaniteLeafNode(path['path'],
                                      CyaniteReader(path['path']))
                else:
                    yield BranchNode(path['path'])

    def fetch_multi(self, nodes, start_time, end_time):

        paths = [node.path for node in nodes]
        data = {}
        for pathlist in chunk(paths, urllength):
            tmpdata = requests.post(urls.metrics,
                                   data={'path': pathlist,
                                           'from': start_time,
                                           'to': end_time}, timeout=fetch_timeout).json()
            if 'error' in tmpdata:
                return (start_time, end_time, end_time - start_time), {}

            if 'series' in data:
                data['series'].update(tmpdata['series'])
            else:
                data = tmpdata

        time_info = data['from'], data['to'], data['step']
        return time_info, data['series']
