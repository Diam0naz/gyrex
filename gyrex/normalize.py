"""GPU name mapping: the tedious but critical part of the index.

Canonical names are gpuhunt's (e.g. "H100", "RTX4090", "A6000", "RTX6000Ada"), since
gpuhunt already normalizes the centralized clouds. Decentralized networks report names like
"NVIDIA GeForce RTX 4090", "NVIDIA 3060" or "NVIDIA RTX A6000"; we map those onto the same
vocabulary. Anything we can't map returns None and is still stored (with gpu_raw) so the
unmapped list can be reviewed and ALIASES extended.
"""

from __future__ import annotations

import re

import gpuhunt

# Canonical gpuhunt names, keyed by their squashed uppercase form.
_KNOWN: dict[str, str] = {
    re.sub(r"[^A-Z0-9]", "", g.name.upper()): g.name for g in gpuhunt.KNOWN_NVIDIA_GPUS
}

# Squashed provider spellings that don't reduce to a gpuhunt name on their own.
ALIASES: dict[str, str] = {
    "RTXA6000": "A6000",
    "RTXA5000": "A5000",
    "RTXA4500": "A4500",
    "RTXA4000": "A4000",
    "RTXA2000": "A2000",
    "QUADRORTX6000": "RTX6000",
    "QUADRORTX8000": "RTX8000",
    "A10G": "A10G",
    "H100NVL": "H100NVL",
    "H200NVL": "H200NVL",
}

# Tokens that carry no model identity: brand words, form factors, memory sizes.
# VRAM is captured separately (vram_from_name / the provider's own field).
_NOISE_TOKEN = re.compile(
    r"^(NVIDIA|GEFORCE|TESLA|GENERATION|BLACKWELL|LAPTOP|GPU|EDITION|NVLINK"
    r"|PCIE\d*|SXM\d*|HBM\d*E?|GDDR\d+X?|\d+GB)$"
)
# Same form factors when glued on without a separator ("H100SXM5"). No memory sizes here:
# "\d+GB$" on "H10080GB" would eat the model number.
_GLUED_SUFFIX = re.compile(r"(PCIE\d*|SXM\d*|HBM\d*E?|NVLINK)$")
_VRAM = re.compile(r"(\d{2,3})\s*GB", re.IGNORECASE)


def _squash(raw: str) -> str:
    tokens = re.split(r"[^A-Z0-9]+", raw.upper())
    return "".join(t for t in tokens if t and not _NOISE_TOKEN.match(t))


def canonical_gpu(raw: str | None) -> str | None:
    """Map a provider GPU name to gpuhunt's canonical name, or None if unknown."""
    if not raw:
        return None
    s = _squash(raw)
    while s:
        for candidate in (s, "RTX" + s):  # Nosana writes "NVIDIA 4090" without "RTX"
            if candidate in ALIASES:
                return ALIASES[candidate]
            if candidate in _KNOWN:
                return _KNOWN[candidate]
        stripped = _GLUED_SUFFIX.sub("", s)
        if stripped == s:
            return None
        s = stripped
    return None


def vram_from_name(raw: str | None) -> float | None:
    """'NVIDIA A100 80GB' -> 80.0. Only trusts 2-3 digit values to skip things like '4GB' noise."""
    if not raw:
        return None
    m = _VRAM.search(raw)
    return float(m.group(1)) if m else None


def default_vram(model: str | None) -> float | None:
    """VRAM from gpuhunt's known-GPU table, or None when the model ships in several sizes
    (A100 40/80, RTX 3060 8/12): guessing there would put offers in the wrong index bucket."""
    sizes = {float(g.memory) for g in gpuhunt.KNOWN_NVIDIA_GPUS if g.name == model}
    return sizes.pop() if len(sizes) == 1 else None
