"""S3: the model gateway (design 3.10): replay keys, request bytes, validation, modes (R-RUN-2), no tools (R-SAFE-1)."""

import base64
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from closeout.gateway import core
from closeout.gateway.core import Gateway, ReplayMiss, hosted_request, ollama_request, replay_key, validate
from closeout.settings import Settings

pytestmark = [pytest.mark.slice("S3")]
VECTORS = Path(__file__).parents[1] / "vectors"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
GOOD = json.dumps(
    {
        "contractor_name": "Inlet Roofing Co.",
        "contractor_license": "13VH00077302",
        "contractor_email": "estimates@inletroof.example",
        "customer_name": "Lena Ostrowski",
        "job_address": "11 Sandpiper Dr",
        "work_type": "roof",
        "completion_date": "2025-12-24",
        "total_amount": 19300.0,
        "warranty_years": 10,
    }
)


def test_reader_a_and_b_never_share_a_key() -> None:
    sha = hashlib.sha256(PNG).hexdigest()
    a = replay_key("A", "m", "d", "invoice", sha)
    assert a != replay_key("B", "m", "d", "invoice", sha)
    assert a != replay_key("A", "m2", "d", "invoice", sha)
    assert a != replay_key("A", "m", "d2", "invoice", sha)
    assert a != replay_key("A", "m", "d", "town_reply", sha)
    assert a != replay_key("A", "m", "d", "invoice", hashlib.sha256(b"x").hexdigest())
    assert a == replay_key("A", "m", "d", "invoice", sha)


@pytest.mark.req("R-SAFE-1")
def test_request_bytes_equal_the_stored_example() -> None:
    body = ollama_request("qwen3-vl:4b-instruct", "invoice", [PNG])
    stored = (VECTORS / "ollama_request_invoice.json").read_text()
    assert json.dumps(body, sort_keys=True, indent=1) + "\n" == stored
    assert "tools" not in body and "tool_choice" not in body
    assert body["options"] == {"temperature": 0, "num_ctx": 8192} and body["think"] is False


@pytest.mark.req("R-SAFE-1")
def test_hosted_request_has_no_tools_and_a_strict_schema() -> None:
    body = hosted_request("hosted-model", "invoice", [PNG])
    assert "tools" not in body and body["response_format"]["json_schema"]["strict"] is True


@pytest.mark.req("R-SAFE-1")
@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("not json", "not_json"),
        ("[1, 2]", "not_an_object"),
        (json.dumps({"contractor_name": "x"}), "missing:total_amount"),
        (json.dumps(dict(json.loads(GOOD), extra="hi")), "extra:extra"),
        (json.dumps(dict(json.loads(GOOD), total_amount="19300")), "type:total_amount"),
        (json.dumps(dict(json.loads(GOOD), work_type="garage")), "enum:work_type"),
        (json.dumps(dict(json.loads(GOOD), warranty_years=True)), "type:warranty_years"),
    ],
)
def test_typed_fields_only(text: str, error: str) -> None:
    out, errors = validate("invoice", text)
    assert out is None and error in errors


def test_valid_answer_passes() -> None:
    out, errors = validate("invoice", GOOD)
    assert errors == [] and out is not None
    assert validate("invoice", json.dumps(dict(json.loads(GOOD), warranty_years=None)))[1] == []


def test_replay_miss_fails_loudly(tmp_path: Path) -> None:
    (tmp_path / "replay").mkdir()
    (tmp_path / "models.lock").write_text(
        json.dumps({"readers": {"A": {"model": "m", "digest": "d"}, "B": {"model": "m2", "digest": "d2"}}})
    )
    gw = Gateway(Settings(closeout_mode="replay"), replay_dir=tmp_path / "replay")
    with pytest.raises(ReplayMiss, match="make record"):
        gw.read(PNG, "image/png", "A")


def test_replay_returns_the_saved_answer(tmp_path: Path) -> None:
    (tmp_path / "replay").mkdir()
    (tmp_path / "models.lock").write_text(
        json.dumps({"readers": {"A": {"model": "m", "digest": "d"}, "B": {"model": "m2", "digest": "d2"}}})
    )
    gw = Gateway(Settings(closeout_mode="replay"), replay_dir=tmp_path / "replay")
    key = replay_key("A", "m", "d", "invoice", hashlib.sha256(PNG).hexdigest())
    (tmp_path / "replay" / "A").mkdir()
    (tmp_path / "replay" / "A" / f"{key}.json").write_text(
        json.dumps({"text": GOOD, "ms": 4500, "tokens_in": 2200, "tokens_out": 120})
    )
    r = gw.read(PNG, "image/png", "A")
    assert r.valid and r.output and r.output["total_amount"] == 19300.0
    assert (r.provider, r.model, r.model_digest, r.ms, r.tokens_in, r.cost_usd) == (
        "replay",
        "m",
        "d",
        4500,
        2200,
        0.0,
    )
    with pytest.raises(ReplayMiss):
        gw.read(PNG, "image/png", "B")


@pytest.mark.req("R-RUN-2")
@pytest.mark.parametrize(
    ("mode", "readers", "providers"),
    [
        ("replay", ["A", "B"], ["replay", "replay"]),
        ("light", ["A"], ["ollama"]),
        ("full", ["A", "B"], ["ollama", "ollama"]),
        ("hosted", ["A", "B"], ["ollama", "hosted"]),
    ],
)
def test_one_setting_picks_the_mode(mode: str, readers: list[str], providers: list[str]) -> None:
    gw = Gateway(Settings(closeout_mode=mode, hosted_model="hm"))  # type: ignore[arg-type]
    assert gw.readers() == readers
    assert [gw.provider_for(r) for r in ("A", "B")][: len(providers)] == providers


@pytest.mark.req("R-RUN-2")
def test_full_and_hosted_modes_call_their_providers(tmp_path: Path) -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if req.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "qwen3-vl:4b-instruct", "digest": "aaa"},
                        {"name": "qwen3-vl:8b-instruct", "digest": "bbb"},
                    ]
                },
            )
        if req.url.path == "/api/chat":
            body = json.loads(req.content)
            assert "tools" not in body
            return httpx.Response(
                200, json={"message": {"content": GOOD}, "prompt_eval_count": 2100, "eval_count": 120}
            )
        if req.url.path == "/v1/chat/completions":
            assert req.headers["authorization"] == "Bearer sk-test"
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": GOOD}}],
                    "usage": {"prompt_tokens": 900, "completion_tokens": 80, "cost_usd": 0.004},
                },
            )
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    s = Settings(
        closeout_mode="hosted",
        ollama_url="http://ollama.test",
        hosted_base_url="http://hosted.test/v1",
        hosted_api_key="sk-test",
        hosted_model="hm",
    )
    gw = Gateway(s, replay_dir=tmp_path, http=http)
    a, b = gw.read(PNG, "image/png", "A"), gw.read(PNG, "image/png", "B")
    assert (a.provider, a.model_digest, a.tokens_in, a.valid) == ("ollama", "aaa", 2100, True)
    assert (b.provider, b.model, b.cost_usd, b.tokens_in) == ("hosted", "hm", 0.004, 900)
    assert calls == ["/api/tags", "/api/chat", "/v1/chat/completions"]


def test_pdf_pages_are_drawn_and_limited(tmp_path: Path) -> None:
    from PIL import Image

    pages = [Image.new("RGB", (850, 1100), "white") for _ in range(4)]
    pdf = tmp_path / "x.pdf"
    pages[0].save(pdf, save_all=True, append_images=pages[1:3])
    imgs = core.page_images(pdf.read_bytes(), "application/pdf")
    assert len(imgs) == 3 and all(i.startswith(b"\x89PNG") for i in imgs)
    pages[0].save(pdf, save_all=True, append_images=pages[1:4])
    with pytest.raises(core.ModelError, match="too_many_pages"):
        core.page_images(pdf.read_bytes(), "application/pdf")
    assert core.page_images(PNG, "image/png") == [PNG]
