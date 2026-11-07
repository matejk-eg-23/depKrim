"""Кэш структуры базы на пять минут в пределах процесса Flask."""

from collections import OrderedDict
from threading import Lock
from time import monotonic


class SchemaCache:
    def __init__(self):
        self.entries = OrderedDict()
        self.lock = Lock()
        self.revision = 0

    def get(self, key, read_schema):
        with self.lock:
            entry = self.entries.get(key)
            if entry and entry[0] > monotonic():
                self.entries.move_to_end(key)
                return entry[1]
            revision = self.revision

        # Чтение внешней базы не должно задерживать обращение к другим схемам.
        value = read_schema()
        with self.lock:
            if revision == self.revision and self.entries.get(key) is entry:
                self.entries[key] = (monotonic() + 300, value)
                self.entries.move_to_end(key)
                if len(self.entries) > 64:
                    self.entries.popitem(last=False)
        return value

    def invalidate(self, source):
        with self.lock:
            self.revision += 1
            for key in list(self.entries):
                if key[0] == source:
                    del self.entries[key]
