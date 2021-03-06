#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Tim Henderson (tadh@case.edu)
#
# This file is part of jpdg a library to generate Program Dependence Graphs
# from JVM bytecode.
#
# Copyright (c) 2014, Tim Henderson, Case Western Reserve University
#   Cleveland, Ohio 44106
#   All Rights Reserved.
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc.,
#   51 Franklin Street, Fifth Floor,
#   Boston, MA  02110-1301
#   USA
# or retrieve version 2.1 at their website:
#   http://www.gnu.org/licenses/lgpl-2.1.html

import sys, os, threading, time, subprocess, fcntl, json
from collections import deque

class Slicer(object):

    def __init__(self, debug=False):
        self.debug = debug
        self.p = subprocess.Popen(['slicebot'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        self.slicer_lock = threading.Lock()
        self.lines = deque()
        self.lines_cv = threading.Condition()
        self.closed = False
        self.read_thread = threading.Thread(target=self.listen)
        self.read_thread.daemon = True
        self.read_thread.start()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        self._close(False)

    def _close(self, from_read=False):
        if self.debug and from_read:
            print >>sys.stderr, "read thread closing it"
        with self.slicer_lock:
            with self.lines_cv:
                if self.closed:
                    if self.debug and from_read:
                        print >>sys.stderr, "read thread bailed"
                    return
                self.closed = True
                self.lines_cv.notifyAll()
        self.p.kill()
        if not from_read:
            self.read_thread.join()
        self.p.wait()
        if self.debug:
            print >>sys.stderr, "closed"
            if from_read:
                print >>sys.stderr, "read thread closed it"

    def load(self, path):
        return self.command('LOAD', path, self.generic_response)

    def generic_response(self):
        cmd, data = self.get_line()
        if cmd == "ERROR":
            raise Exception(data)
        elif cmd != "OK":
            raise Exception, "bad command recieved %s %s" % (cmd, data)
        else:
            return True

    def candidates(self, prefix):
        return self.command('CANDIDATES', prefix, self.candidates_response)

    def candidates_response(self):
        cmd, data = self.get_line()
        if cmd == "ERROR":
            raise Exception(data)
        elif cmd != "CANDIDATES":
            raise Exception, "bad command recieved %s %s" % (cmd, data)
        else:
            lines = [
                line.split(', ', 1)
                for line in data.strip().split('\n')
                if line and ', ' in line
            ]
            return [
                {'label': row[1], 'count': int(row[0])}
                for row in lines
            ]

    def slice(self, prefix, direction=None, filtered_edges=None):
        args = ['-p', prefix]
        if direction is not None:
            args.append('-d')
            args.append(direction)
        if filtered_edges is not None:
            for e in filtered_edges:
                args.append('-e')
                args.append(e)
        return self.command('SLICE', ' '.join(args), self.slice_response)

    def slice_response(self):
        cmd, data = self.get_line()
        if cmd == "ERROR":
            raise Exception(data)
        elif cmd != "GRAPHS":
            raise Exception, "bad command recieved %s %s" % (cmd, data)
        else:
            return data

    def node(self, nid):
        args = [str(nid)]
        return self.command('NODE', ' '.join(args), self.node_response)

    def node_response(self):
        cmd, data = self.get_line()
        if cmd == "ERROR":
            raise Exception(data)
        elif cmd != "NODE":
            raise Exception, "bad command recieved %s %s" % (cmd, data)
        else:
            return json.loads(data)

    def edge(self, u, v):
        args = [str(u), str(v)]
        return self.command('EDGE', ' '.join(args), self.edge_response)

    def edge_response(self):
        cmd, data = self.get_line()
        if cmd == "ERROR":
            raise Exception(data)
        elif cmd != "EDGE":
            raise Exception, "bad command recieved %s %s" % (cmd, data)
        else:
            return json.loads(data)

    def sub_graph(self, nodes, filtered_edges=None):
        args = list()
        if filtered_edges is not None:
            for e in filtered_edges:
                args.append('-e')
                args.append(e)
        args += [str(nid) for nid in nodes]
        return self.command('SUBGRAPH', ' '.join(args), self.subgraph_response)

    def subgraph_response(self):
        cmd, data = self.get_line()
        if cmd == "ERROR":
            raise Exception(data)
        elif cmd != "GRAPH":
            raise Exception, "bad command recieved %s %s" % (cmd, data)
        else:
            return data

    def partition(self, attr, filtered_edges=None):
        args = list()
        if filtered_edges is not None:
            for e in filtered_edges:
                args.append('-e')
                args.append(e)
        args += ['-a', attr]
        return self.command('PARTITION', ' '.join(args), self.slice_response)

    def projected_partition(self, prefix, attr, filtered_edges=None):
        args = list()
        if filtered_edges is not None:
            for e in filtered_edges:
                args.append('-e')
                args.append(e)
        args += ['-a', attr, '-p', prefix]
        return self.command('PROJECTED-PARTITION', ' '.join(args), self.slice_response)

    def command(self, cmd, data, response):
        with self.slicer_lock:
            msg = cmd + " " + data.encode('base64').replace('\n', '') + '\n'
            self.p.stdin.write(msg)
            return response()

    def listen(self):
        chunk = ''
        while True:
            while "\n" not in chunk:
                try:
                    data = os.read(self.p.stdout.fileno(), 4096*64)
                    if not data:
                        self._close(True)
                        return
                    chunk += data
                except Exception, e:
                    print >>sys.stderr, e, type(e)
                    self._close(True)
                    return
            line, chunk = chunk.split('\n', 1)
            with self.lines_cv:
                self.lines.append(line)
                self.lines_cv.notify()

    def get_line(self):
        with self.lines_cv:
            while len(self.lines) <= 0:
                if self.closed:
                    raise Exception, "queued connection closed"
                self.lines_cv.wait()
            line = self.lines.popleft()
        return self.process_line(line)

    def process_line(self, line):
        split = line.split(' ', 1)
        command = split[0]
        rest = None
        if len(split) > 1:
            rest = split[1].decode('base64')

        return command, rest

def _loop(slicer):
    while True:
        try:
            line = raw_input('> ')
        except:
            break
        split = line.split(' ', 1)
        command = split[0]
        data = None
        try:
            if len(split) > 1:
                data = split[1]
            if command == 'load' and data is not None:
                print slicer.load(data)
            elif command == 'candidates' and data is not None:
                for c in slicer.candidates(data):
                    print c
            elif command == 'slice' and data is not None:
                print slicer.slice(data, filtered_edges=['ddg', 'cdg'])
            else:
                print slicer.command(command.upper(), data if data is not None else
                        '', slicer.generic_response)
        except Exception, e:
            print >>sys.stderr, type(e), e
        print

def main():
    slicer = Slicer()
    try:
       _loop(slicer)
    finally:
        slicer.close()

if __name__ == '__main__':
    main()

