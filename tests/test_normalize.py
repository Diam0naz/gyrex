import pytest

from gyrex.normalize import canonical_gpu, default_vram, vram_from_name


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("NVIDIA GeForce RTX 4090", "RTX4090"),
        ("NVIDIA 4090", "RTX4090"),
        ("NVIDIA 3060", "RTX3060"),
        ("NVIDIA 4070 Ti", "RTX4070Ti"),
        ("nvidia-3090", "RTX3090"),
        ("NVIDIA RTX A6000", "A6000"),
        ("NVIDIA RTX 6000 Ada Generation", "RTX6000Ada"),
        ("NVIDIA RTX PRO 6000 Blackwell", "RTXPRO6000"),
        ("NVIDIA A100 80GB", "A100"),
        ("A100-SXM4-80GB", "A100"),
        ("NVIDIA H100 80GB HBM3", "H100"),
        ("H100_SXM5", "H100"),
        ("h100-80gb", "H100"),
        ("H100 NVL", "H100NVL"),
        ("H100SXM5", "H100"),
        ("NVIDIA L40S", "L40S"),
        ("Tesla T4", "T4"),
        ("RTX4090", "RTX4090"),
        ("Mystery Accelerator X", None),
        ("", None),
        (None, None),
    ],
)
def test_canonical_gpu(raw, expected):
    assert canonical_gpu(raw) == expected


def test_vram():
    assert vram_from_name("NVIDIA A100 80GB") == 80.0
    assert vram_from_name("NVIDIA 4090") is None
    assert default_vram("RTX4090") == 24.0
    assert default_vram("A100") is None  # 40 and 80 GB variants
    assert default_vram("NOPE") is None
    assert default_vram(None) is None
