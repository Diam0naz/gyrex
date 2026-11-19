import pytest

from gyrex.normalize import canonical_gpu, default_vram, gpu_count_from_name, snap_vram, vram_from_name


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
        ("NVIDIA 4090 Community", "RTX4090"),
        ("NVIDIA 4090 SEV TEE", "RTX4090"),
        ("ember-drift NVIDIA 4090", "RTX4090"),
        ("NVIDIA 4000/A4000 Community", "A4000"),
        ("NVIDIA 5000/A5000", "A5000"),
        ("NVIDIA 6000 Ada Community", "RTX6000Ada"),
        ("NVIDIA A100 40GB Community", "A100"),
        ("NVIDIA Pro 6000 (6x)", "RTXPRO6000"),
        ("NVIDIA Pro 6000 (SOC2)", "RTXPRO6000"),
        ("NVIDIA Pro 6000 (WE) Community", "RTXPRO6000"),
        ("NVIDIA Pro 6000 Server Edition (CC)", "RTXPRO6000"),
        ("NVIDIA RTX PRO 6000 Blackwell Server Edition", "RTXPRO6000"),
        ("(Reserved)", None),  # names no GPU
        ("Community", None),
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


def test_snap_vram_only_moves_near_misses():
    assert snap_vram(95.0, "RTXPRO6000") == 96.0
    assert snap_vram(48.0, "RTXPRO6000") == 48.0  # a different card, not a rounding gap
    assert snap_vram(12.0, "RTX3060") == 12.0  # several sizes: nothing to snap to
    assert snap_vram(None, "RTX4090") is None


@pytest.mark.parametrize(
    "raw,count",
    [
        ("NVIDIA Pro 6000 (6x)", 6),
        ("4x NVIDIA H100", 4),
        ("NVIDIA 4090", 1),
        ("NVIDIA 4090 SEV TEE", 1),
        ("Enterprise 8xA5000", 1),  # glued to the model: left for review, stays unmapped
        ("(0x)", 1),
        (None, 1),
    ],
)
def test_gpu_count_from_name(raw, count):
    assert gpu_count_from_name(raw) == count
