"""Real footage ingestion: video file (or image folder) -> sampled frames -> VLM captions -> Frames.

Sampling every N seconds instead of every frame is the main cost lever: consecutive
frames at 25-30 fps are near-duplicates, and a security event lasts seconds to minutes.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

from .config import PropertyConfig
from .models import Frame
from .perception.vlm import Captioner
from .simulator import TelemetrySynthesizer

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def sample_frames(source: str | Path, every_s: float = 2.0, max_frames: Optional[int] = None) -> Iterator[tuple[float, bytes]]:
    """Yield (offset_seconds, jpeg_bytes). `source` is a video file or a folder of images (1 image = 1 sample)."""
    source = Path(source)
    if source.is_dir():
        images = sorted(p for p in source.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        for i, path in enumerate(images[:max_frames] if max_frames else images):
            yield i * every_s, path.read_bytes()
        return

    import cv2

    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise FileNotFoundError(f"cannot open video {source}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(int(round(fps * every_s)), 1)
    index = emitted = 0
    try:
        while cap.grab():  # grab() skips decoding of frames we don't sample
            if index % step == 0:
                ok, image = cap.retrieve()
                if not ok:
                    break
                ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    yield index / fps, buf.tobytes()
                    emitted += 1
                    if max_frames and emitted >= max_frames:
                        break
            index += 1
    finally:
        cap.release()


def caption_video(
    source: str | Path,
    captioner: Captioner,
    config: PropertyConfig,
    *,
    start: datetime,
    zone: str,
    every_s: float = 2.0,
    max_frames: Optional[int] = None,
    save_dir: Optional[str | Path] = None,
    first_frame_id: int = 1,
) -> Iterator[Frame]:
    """Caption sampled frames and wrap them as pipeline Frames (telemetry: drone hovering over `zone`)."""
    synth = TelemetrySynthesizer(config)
    out_dir = Path(save_dir) if save_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    for i, (offset, jpeg) in enumerate(sample_frames(source, every_s, max_frames)):
        at = start + timedelta(seconds=offset)
        image_path = None
        if out_dir:
            image_path = str(out_dir / f"frame_{first_frame_id + i:05d}.jpg")
            Path(image_path).write_bytes(jpeg)
        yield Frame(
            frame_id=first_frame_id + i,
            timestamp=at,
            description=captioner.caption(jpeg, zone_hint=zone),
            telemetry=synth.next(at, zone),
            source=captioner.name,
            image_path=image_path,
        )
