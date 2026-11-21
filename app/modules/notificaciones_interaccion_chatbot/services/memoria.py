"""Bounded process-local state. No distributed or durable guarantees."""
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
from threading import Lock, RLock
import time
from uuid import uuid4

from fastapi import HTTPException


def tamano(value):
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


@dataclass
class Conversacion:
    id: str
    propietario: tuple
    tocada: float
    historial: list = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)


class MemoriaConversaciones:
    def __init__(self, reloj=time.monotonic):
        self.reloj = reloj
        self.lock = RLock()
        self.sesiones = {}
        self.ventanas = {}
        self.activas = 0

    def _limpiar(self, cfg):
        ahora = self.reloj()
        for id, c in list(self.sesiones.items()):
            if ahora - c.tocada >= cfg.session_ttl_seconds and not c.lock.locked():
                del self.sesiones[id]
        for key, queue in list(self.ventanas.items()):
            while queue and ahora - queue[0] >= 60:
                queue.popleft()
            if not queue:
                del self.ventanas[key]

    def _bytes(self):
        # Conservative per-entry overhead in addition to serialized history.
        return sum(4096 + tamano(c.historial) for c in self.sesiones.values())

    def consumir(self, owner, cfg, *, proveedor=False):
        with self.lock:
            self._limpiar(cfg)
            keys = [(('paciente', *owner[:3]), cfg.rate_limit_per_minute),
                    (('tenant', owner[0]), cfg.tenant_rate_limit_per_minute),
                    (('global',), cfg.global_rate_limit_per_minute)]
            if proveedor:
                keys = [(('proveedor',), cfg.global_rate_limit_per_minute)]
            if len(self.ventanas) + len(keys) > 10000:
                raise HTTPException(429, "Límite temporal del chatbot", headers={"Retry-After": "60"})
            if any(len(self.ventanas.get(k, ())) >= n for k, n in keys):
                raise HTTPException(429, "Límite de consumo del chatbot", headers={"Retry-After": "60"})
            for key, _ in keys:
                self.ventanas.setdefault(key, deque()).append(self.reloj())

    def crear(self, owner, cfg):
        with self.lock:
            self._limpiar(cfg)
            if (len(self.sesiones) >= cfg.max_sessions
                or sum(c.propietario[:3] == owner[:3] for c in self.sesiones.values()) >= cfg.max_sessions_per_patient
                or self._bytes() + 4098 > cfg.max_memory_bytes):
                raise HTTPException(429, "Capacidad temporal del chatbot alcanzada")
            c = Conversacion(str(uuid4()), owner, self.reloj())
            self.sesiones[c.id] = c
            return c

    @contextmanager
    def usar(self, id, owner, cfg):
        with self.lock:
            self._limpiar(cfg)
            c = self.sesiones.get(str(id))
            if c is None or c.propietario != owner:
                raise HTTPException(404, "Conversación no disponible")
            if self.activas >= cfg.max_concurrent_requests or not c.lock.acquire(False):
                raise HTTPException(409, "Conversación ocupada; vuelve a intentarlo")
            self.activas += 1
        try:
            yield c
        finally:
            with self.lock:
                c.tocada = self.reloj()
                self.activas -= 1
                c.lock.release()

    def guardar(self, c, turno, cfg):
        with self.lock:
            historial = c.historial + turno
            # Whole user/assistant pairs; never persist tools, raw cards or reasoning.
            while historial and (len(historial) > cfg.max_history_messages
                                 or tamano(historial) > cfg.max_context_bytes // 2):
                historial = historial[2:]
            viejo = c.historial
            c.historial = historial
            if self._bytes() > cfg.max_memory_bytes:
                c.historial = viejo
                raise HTTPException(429, "Capacidad temporal del chatbot alcanzada")

    def borrar(self, c):
        with self.lock:
            self.sesiones.pop(c.id, None)
