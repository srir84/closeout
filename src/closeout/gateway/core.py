"""The model gateway: the only code that talks to an AI model (design 3.10, rule 2).

- Models get no tools. They return typed fields as JSON and nothing else.
- One model's text never goes into another model's prompt.
- Every call is logged (R-OBS-1) by the caller from the returned ReadResult.

Providers: replay (saved answers, the default), ollama (local models), hosted (OpenAI-compatible API).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from closeout.settings import Settings, get_settings

PROMPTS = Path(__file__).parent / "prompts"
PROMPT_VERSION = {"invoice": "invoice_v1", "town_reply": "town_reply_v1"}
PREPROCESSING_VERSION = "pp1"  # images: the uploaded bytes as-is; PDFs: up to 3 pages at 150 dpi as PNG
SETTINGS = {"temperature": 0, "num_ctx": 8192}
THINK = False
MAX_PDF_PAGES = 3
Reader = Literal["A", "B"]
Purpose = Literal["invoice", "town_reply"]


class ReplayMiss(Exception):
    """No saved answer for this request. Raised as a non-retryable error with the key and how to re-record."""


class ModelError(Exception):
    pass


class ReadResult(BaseModel):
    reader: Reader
    purpose: Purpose
    provider: str
    model: str
    model_digest: str
    prompt_version: str
    replay_key: str
    text: str
    output: dict[str, Any] | None
    valid: bool
    errors: list[str]
    ms: int
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float


@lru_cache
def prompt(purpose: Purpose) -> tuple[str, str, dict[str, Any], str]:
    v = PROMPT_VERSION[purpose]
    system = (PROMPTS / f"{v}.system.md").read_text()
    user = (PROMPTS / f"{v}.user.md").read_text()
    schema_text = (PROMPTS / f"{v}.schema.json").read_text()
    return system, user, json.loads(schema_text), hashlib.sha256(schema_text.encode()).hexdigest()


def page_images(data: bytes, mime: str) -> list[bytes]:
    """What the model sees: the image itself, or each PDF page drawn at 150 dpi (at most 3 pages)."""
    if mime != "application/pdf":
        return [data]
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        if len(pdf) > MAX_PDF_PAGES:
            raise ModelError("too_many_pages")
        out = []
        for i in range(len(pdf)):
            img = pdf[i].render(scale=150 / 72).to_pil()  # pyright: ignore[reportArgumentType] (takes a float)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            out.append(buf.getvalue())
        return out
    finally:
        pdf.close()


def replay_key(reader: str, model: str, digest: str, purpose: Purpose, file_sha256: str) -> str:
    _, _, _, schema_sha = prompt(purpose)
    parts = [
        reader,
        model,
        digest,
        PROMPT_VERSION[purpose],
        schema_sha,
        file_sha256,
        PREPROCESSING_VERSION,
        json.dumps(SETTINGS, sort_keys=True) + f"think={THINK}",
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def ollama_request(model: str, purpose: Purpose, images: list[bytes]) -> dict[str, Any]:
    system, user, schema, _ = prompt(purpose)
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user, "images": [base64.b64encode(i).decode() for i in images]},
        ],
        "stream": False,
        "think": THINK,
        "options": dict(SETTINGS),
        "format": schema,
    }


def hosted_request(model: str, purpose: Purpose, images: list[bytes]) -> dict[str, Any]:
    system, user, schema, _ = prompt(purpose)
    content: list[dict[str, Any]] = [{"type": "text", "text": user}]
    for i in images:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + base64.b64encode(i).decode()},
            }
        )
    return {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": PROMPT_VERSION[purpose], "strict": True, "schema": schema},
        },
    }


def validate(purpose: Purpose, text: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Typed fields only: the answer must be one JSON object with exactly the schema's keys and types."""
    _, _, schema, _ = prompt(purpose)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None, ["not_json"]
    if not isinstance(data, dict):
        return None, ["not_an_object"]
    errors = []
    props = schema["properties"]
    for k in schema["required"]:
        if k not in data:
            errors.append(f"missing:{k}")
    for k, v in data.items():
        if k not in props:
            errors.append(f"extra:{k}")
            continue
        t = props[k].get("type")
        types = t if isinstance(t, list) else [t]
        ok = any(_is(v, tt) for tt in types)
        if not ok:
            errors.append(f"type:{k}")
        elif "enum" in props[k] and v not in props[k]["enum"]:
            errors.append(f"enum:{k}")
    return (data if not errors else None), errors


def _is(v: object, t: str) -> bool:
    if t == "string":
        return isinstance(v, str)
    if t == "number":
        return isinstance(v, int | float) and not isinstance(v, bool)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "null":
        return v is None
    return False


class Locked(BaseModel):
    model: str
    digest: str


def models_lock(path: Path | None = None) -> dict[str, Locked]:
    p = path or Path(get_settings().replay_dir).parent / "models.lock"
    if not p.exists():
        for alt in (Path("/app/evals/models.lock"), Path(__file__).parents[3] / "evals" / "models.lock"):
            if alt.exists():
                p = alt
                break
    data = json.loads(p.read_text())
    return {k: Locked(**v) for k, v in data["readers"].items()}


class Gateway:
    def __init__(
        self,
        settings: Settings | None = None,
        replay_dir: Path | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self.s = settings or get_settings()
        self.replay_dir = replay_dir or self._find_replay_dir()
        self.http = http or httpx.Client(timeout=120)
        self._digests: dict[str, str] = {}

    def _find_replay_dir(self) -> Path:
        for p in (
            Path(self.s.replay_dir),
            Path("/app/evals/replay"),
            Path(__file__).parents[3] / "evals" / "replay",
        ):
            if p.exists():
                return p
        return Path(self.s.replay_dir)

    def readers(self) -> list[Reader]:
        return ["A"] if self.s.closeout_mode == "light" else ["A", "B"]

    def provider_for(self, reader: Reader) -> str:
        mode = self.s.closeout_mode
        if mode == "replay":
            return "replay"
        if mode == "hosted" and reader == "B":
            return "hosted"
        return "ollama"

    def model_for(self, reader: Reader) -> str:
        if self.provider_for(reader) == "hosted":
            return self.s.hosted_model
        return self.s.reader_a if reader == "A" else self.s.reader_b

    def digest(self, reader: Reader) -> str:
        provider = self.provider_for(reader)
        model = self.model_for(reader)
        if provider == "replay":
            return models_lock(self.replay_dir.parent / "models.lock")[reader].digest
        if provider == "hosted":
            return "hosted:" + model
        if model not in self._digests:
            r = self.http.get(self.s.ollama_url.rstrip("/") + "/api/tags", timeout=10)
            r.raise_for_status()
            for m in r.json().get("models", []):
                self._digests[m["name"]] = m["digest"]
        if model not in self._digests:
            raise ModelError(f"model {model} is not installed in Ollama (run: ollama pull {model})")
        return self._digests[model]

    def read(self, data: bytes, mime: str, reader: Reader, purpose: Purpose = "invoice") -> ReadResult:
        provider = self.provider_for(reader)
        if provider == "replay":
            model = models_lock(self.replay_dir.parent / "models.lock")[reader].model
        else:
            model = self.model_for(reader)
        digest = self.digest(reader)
        key = replay_key(reader, model, digest, purpose, hashlib.sha256(data).hexdigest())
        t0 = time.monotonic()
        if provider == "replay":
            path = self.replay_dir / reader / f"{key}.json"
            if not path.exists():
                raise ReplayMiss(
                    f"no saved answer for reader {reader}, key {key}. Re-record on a Mac with "
                    "`make record` (needs Ollama), or switch CLOSEOUT_MODE to light or full."
                )
            saved = json.loads(path.read_text())
            text, tin, tout, cost = saved["text"], saved.get("tokens_in"), saved.get("tokens_out"), 0.0
            ms = int(saved.get("ms", 0))
        elif provider == "ollama":
            body = ollama_request(model, purpose, page_images(data, mime))
            r = self.http.post(self.s.ollama_url.rstrip("/") + "/api/chat", json=body, timeout=120)
            r.raise_for_status()
            j = r.json()
            text, tin, tout, cost = (
                j["message"]["content"],
                j.get("prompt_eval_count"),
                j.get("eval_count"),
                0.0,
            )
            ms = int((time.monotonic() - t0) * 1000)
        else:
            body = hosted_request(model, purpose, page_images(data, mime))
            r = self.http.post(
                self.s.hosted_base_url.rstrip("/") + "/chat/completions",
                json=body,
                timeout=120,
                headers={"Authorization": f"Bearer {self.s.hosted_api_key}"},
            )
            r.raise_for_status()
            j = r.json()
            text = j["choices"][0]["message"]["content"]
            usage = j.get("usage") or {}
            tin, tout = usage.get("prompt_tokens"), usage.get("completion_tokens")
            cost = float(usage.get("cost_usd", 0.0) or 0.0)
            ms = int((time.monotonic() - t0) * 1000)
        output, errors = validate(purpose, text)
        return ReadResult(
            reader=reader,
            purpose=purpose,
            provider=provider,
            model=model,
            model_digest=digest,
            prompt_version=PROMPT_VERSION[purpose],
            replay_key=key,
            text=text,
            output=output,
            valid=output is not None,
            errors=errors,
            ms=ms,
            tokens_in=tin,
            tokens_out=tout,
            cost_usd=cost,
        )

    def save_replay(self, res: ReadResult) -> Path:
        """`make record` only: save a live answer as a replay file."""
        path = self.replay_dir / res.reader / f"{res.replay_key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "text": res.text,
                    "model": res.model,
                    "model_digest": res.model_digest,
                    "prompt_version": res.prompt_version,
                    "ms": res.ms,
                    "tokens_in": res.tokens_in,
                    "tokens_out": res.tokens_out,
                },
                indent=1,
                sort_keys=True,
            )
            + "\n"
        )
        return path
