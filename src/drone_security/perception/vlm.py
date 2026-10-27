"""Vision-language captioners: real image -> security-focused caption.

The caption then flows through the same analyzer -> tracker -> rules -> index path as
simulated captions, so the pipeline is identical for real and simulated footage.

* `ClaudeVisionCaptioner` - Claude vision; best detail (make/model, clothing, actions).
* `BlipCaptioner`         - Salesforce BLIP via Hugging Face; runs locally/offline on
                            the dock's GPU, coarser captions ("a truck parked in a driveway").
"""
from __future__ import annotations

from typing import Optional, Protocol

from ..llm import ClaudeClient, image_block

CAPTION_PROMPT = """This is a frame from a security drone patrolling a property{zone_hint}.
Describe it in ONE sentence for a security log. Mention every person (clothing colour, what they carry,
what they are doing), every vehicle (colour, make and model if readable, whether entering/leaving/parked),
animals, and open gates or doors. Use plain words, no speculation. If nothing notable is visible,
reply with a short scene description such as "Empty driveway, no activity"."""


class Captioner(Protocol):
    name: str

    def caption(self, image_bytes: bytes, zone_hint: Optional[str] = None) -> str: ...


class ClaudeVisionCaptioner:
    name = "vlm:claude"

    def __init__(self, client: Optional[ClaudeClient] = None):
        self.client = client or ClaudeClient(effort="low")

    def caption(self, image_bytes: bytes, zone_hint: Optional[str] = None) -> str:
        hint = f", camera over the {zone_hint.replace('_', ' ')}" if zone_hint else ""
        return self.client.text(
            system="You write concise, factual captions of security camera frames.",
            content=[image_block(image_bytes), {"type": "text", "text": CAPTION_PROMPT.format(zone_hint=hint)}],
            max_tokens=2000,
        )


class BlipCaptioner:
    """Local captioning with BLIP (conditional captioning steered by a short text prefix)."""

    name = "vlm:blip"

    def __init__(self, model_id: str = "Salesforce/blip-image-captioning-large", device: Optional[str] = None):
        import torch
        from transformers import BlipForConditionalGeneration, BlipProcessor

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.processor = BlipProcessor.from_pretrained(model_id)
        self.model = BlipForConditionalGeneration.from_pretrained(model_id).to(self.device)

    def caption(self, image_bytes: bytes, zone_hint: Optional[str] = None) -> str:
        import io

        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        inputs = self.processor(image, "a security camera photo of", return_tensors="pt").to(self.device)
        output = self.model.generate(**inputs, max_new_tokens=40)
        text = self.processor.decode(output[0], skip_special_tokens=True)
        text = text.replace("a security camera photo of", "").strip()
        return f"{text[:1].upper()}{text[1:]}" + (f" at {zone_hint.replace('_', ' ')}" if zone_hint else "")


def get_captioner(name: str) -> Captioner:
    if name == "claude":
        return ClaudeVisionCaptioner()
    if name == "blip":
        return BlipCaptioner()
    raise ValueError(f"unknown captioner '{name}' (use 'claude' or 'blip')")
