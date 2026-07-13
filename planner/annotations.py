"""Inline annotation parser used by the deterministic extractor.

The Phase-1 sample script format uses ``[meta:kind ...]`` blocks to seed
character / location / prop metadata without invoking an LLM. Each
annotation block is a single line beginning with ``[meta:`` and ending
with ``]``; key/value pairs are separated by ``;`` and ``:``.

Example::

    [meta:character lin_xia]
    appearance: 清瘦，鹅蛋脸，黑色锁骨发
    wardrobe: 白色衬衫，浅灰西装外套，细银项链
    negative: 不要夸张妆容，不要网红脸

This module only parses — downstream code decides how to merge these
seeds with the script-derived raw extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


META_HEADER_PATTERN = re.compile(r"^\s*\[meta:(\w+)\s+([^\]]+?)\s*\]\s*$")
KV_PATTERN = re.compile(r"^\s*([\w]+)\s*[:：]\s*(.+?)\s*$")


@dataclass
class MetaAnnotation:
    kind: str
    id: str
    fields: Dict[str, str] = field(default_factory=dict)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.fields.get(key, default)


def parse_annotations(script_text: str) -> List[MetaAnnotation]:
    """Extract all ``[meta:*]`` annotations from a raw script."""

    annotations: List[MetaAnnotation] = []
    pending: Optional[MetaAnnotation] = None

    for line in script_text.splitlines():
        header = META_HEADER_PATTERN.match(line)
        if header:
            kind = header.group(1).strip()
            ident = header.group(2).strip()
            pending = MetaAnnotation(kind=kind, id=ident)
            annotations.append(pending)
            continue
        if pending is None:
            continue
        kv = KV_PATTERN.match(line)
        if kv:
            pending.fields[kv.group(1).strip()] = kv.group(2).strip()
        elif line.strip() == "":
            pending = None
    return annotations