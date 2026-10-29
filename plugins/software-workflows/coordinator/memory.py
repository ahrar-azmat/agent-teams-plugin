"""Launch admission from kernel pressure, sampled only when work asks to start."""
import ctypes
import math
import os
import sys
import time

from .client import CoordinatorError


# On the 24 GiB host, Jetsam occurred at compressor ratios .68-.70 even
# without a warning pressure level. .50 reserves 18 points before that lower
# observation; .62 reserves six. These are adjustable admission thresholds,
# never a reason to interrupt an existing turn.
DEFAULT_WARN = .50
DEFAULT_CRITICAL = .62
CURRENT = None


def setting(name, default):
    return os.environ.get("SOFTWARE_WORKFLOWS_" + name,
                          os.environ.get("CLAUDE_PLUGIN_OPTION_" + name, default))


def number(name):
    lib = ctypes.CDLL(None, use_errno=True)
    size = ctypes.c_size_t()
    if lib.sysctlbyname(name.encode(), None, ctypes.byref(size), None, 0):
        raise OSError(ctypes.get_errno(), "Kernel memory reading unavailable")
    buffer = ctypes.create_string_buffer(size.value)
    if lib.sysctlbyname(name.encode(), buffer, ctypes.byref(size), None, 0):
        raise OSError(ctypes.get_errno(), "Kernel memory reading unavailable")
    return int.from_bytes(buffer.raw[:size.value], sys.byteorder)


def reading():
    if sys.platform != "darwin":
        return {"supported": False, "platform": sys.platform}
    pages = number("vm.compressor.pages_compressed")
    limit = number("vm.compressor.pages_compressed_limit")
    segments = number("vm.compressor.segment.total")
    segment_limit = number("vm.compressor.segment.limit")
    return {"supported": True, "pressure": number("kern.memorystatus_vm_pressure_level"),
            "compressor_ratio": max(pages / limit, segments / segment_limit),
            "pages": pages, "pages_limit": limit, "segments": segments, "segments_limit": segment_limit}


class Memory:
    def __init__(self, daemon=None):
        self.daemon = daemon
        self.level = "normal"
        self.sample = None
        self.error = None
        try:
            self.warn = float(setting("MEMORY_WARN_RATIO", DEFAULT_WARN))
            self.critical = float(setting("MEMORY_CRITICAL_RATIO", DEFAULT_CRITICAL))
            # Five ratio points avoid oscillating launches at the warning line.
            self.hysteresis = float(setting("MEMORY_HYSTERESIS", .05))
            if not (all(math.isfinite(v) for v in (self.warn, self.critical, self.hysteresis))
                    and 0 < self.hysteresis < self.warn < self.critical < 1):
                raise ValueError()
        except ValueError:
            self.error = "Invalid memory thresholds: require 0 < hysteresis < warning < critical < 1"

    def classify(self, sample):
        """The launch level a sample sets, with hysteresis from the current one, and the input that decided it."""
        previous = self.level
        if sample["supported"]:
            ratio, pressure = sample["compressor_ratio"], sample["pressure"]
            critical = self.critical - (self.hysteresis if previous == "critical" else 0)
            warning = self.warn - (self.hysteresis if previous != "normal" else 0)
            if pressure >= 4 or ratio >= critical:
                return "critical", "kernel critical" if pressure >= 4 else "compressor critical"
            if ratio >= warning:
                return "warning", "compressor warning"
        return "normal", "below thresholds"

    def admits(self):
        """Whether check would admit a launch now, read without recording, requesting or raising anything: a start this
        gate refused is due again once it would (recovery.gate_reopened)."""
        if self.error:
            return False
        try:
            return self.classify(reading())[0] == "normal"
        except (OSError, ZeroDivisionError):
            return False

    def check(self):
        # Its refusals name the gate (resource): the retry policy asks admits before their backoff has passed.
        if self.error:
            raise CoordinatorError("RESOURCE_PAUSED", self.error, {"retryable": True, "resource": "memory"}, True)
        try:
            sample = reading()
        except (OSError, ZeroDivisionError) as exc:
            raise CoordinatorError("RESOURCE_PAUSED", "Memory pressure could not be read; launch deferred",
                                   {"retryable": True, "cause": type(exc).__name__, "resource": "memory"}, True) from exc
        previous = self.level
        level, deciding = self.classify(sample)
        self.level, self.sample = level, dict(sample, observed=time.time(), deciding_input=deciding)
        if self.daemon and previous != level:
            self.daemon.store.event("resource/memory", None, self.status())
        if self.daemon and (level != "normal" or sample.get("pressure") == 2):
            # Warning level 2 alone is common during healthy interactive use.
            # It can request idle reclamation, but cannot defer a launch. Park's
            # own task rechecks indexed eligibility under its serialization.
            parking = self.daemon.parking
            for ident in sorted(parking.activity, key=parking.activity.get):
                parking.request(ident)
        if level != "normal":
            code = "RESOURCE_EXHAUSTED" if level == "critical" else "RESOURCE_PAUSED"
            raise CoordinatorError(code, "Native launch deferred by kernel memory pressure",
                                   dict(self.status(), retryable=True, resource="memory"), True)
        return self.sample

    def status(self):
        return {"level": self.level, "sample": self.sample, "warning_ratio": getattr(self, "warn", None),
                "critical_ratio": getattr(self, "critical", None), "hysteresis": getattr(self, "hysteresis", None),
                "configuration_error": self.error}


def before_launch():
    return (CURRENT or Memory()).check()
