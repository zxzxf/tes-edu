"""真数字人（腾讯云 IVH SaaS 播报视频制作）接入单测。

全部 mock 掉 httpx / ffmpeg，**不联网、不消耗视频时长额度、不读任何真实密钥**。
异步逻辑统一用 asyncio.run 驱动（与仓库其余用例一致，无需 pytest-asyncio）。
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import urllib.parse
from unittest.mock import patch

import httpx
import pytest

from app.core.config import settings
from app.services import ivh_client, video_generator


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _restore_settings():
    """每个用例结束后恢复 settings，避免污染 743 基线里的其它用例。"""
    keys = [
        "digital_human_enabled", "digital_human_impl", "ivh_appkey",
        "ivh_accesstoken", "ivh_ref_photo_url", "ivh_driver_type",
        "ivh_audio_public_base", "ivh_max_ssml_chars", "ivh_timbre_key",
        "ivh_speed", "ivh_volume", "ivh_request_timeout", "ivh_poll_interval",
        "ivh_poll_timeout", "ivh_download_timeout", "ivh_normalize_video",
    ]
    saved = {k: getattr(settings, k) for k in keys}
    yield
    for k, v in saved.items():
        setattr(settings, k, v)


# ---------------------------------------------------------------- 签名 / URL

def test_signature_matches_official_algorithm():
    """签名原文必须按字典序 appkey -> timestamp，且 base64 后 urlencode。"""
    ts = 1717639699
    appkey, token = "ak_test", "secret_test"
    expected = urllib.parse.quote(
        base64.b64encode(
            hmac.new(
                token.encode(), f"appkey={appkey}&timestamp={ts}".encode(), hashlib.sha256
            ).digest()
        ).decode(),
        safe="",
    )
    assert ivh_client.build_signature(appkey, token, ts) == expected


def test_signature_changes_with_timestamp():
    """签名必须随请求动态变化，静态 sign 不可用。"""
    a = ivh_client.build_signature("ak", "tk", 1700000000)
    b = ivh_client.build_signature("ak", "tk", 1700000001)
    assert a != b
    assert ivh_client.build_signature("ak", "tk", 1700000000) == a


def test_signed_url_contains_common_params():
    url = ivh_client.signed_url(
        ivh_client.PHOTO_SUBMIT_PATH, "ak/x", "tk", "https://gw.tvs.qq.com", 1700000000
    )
    assert url.startswith("https://gw.tvs.qq.com/v2/ivh/videomaker/broadcastservice/phototovideonotrain?")
    assert "appkey=ak%2Fx" in url
    assert "timestamp=1700000000" in url
    assert "signature=" in url
    assert "sign=" not in url  # 绝不使用平台给的静态 sign


# ---------------------------------------------------------------- SSML 清洗

def test_normalize_ssml_merges_lines_escapes_and_truncates():
    out = ivh_client.normalize_ssml("第一步：移项\n2x + 3 = 7\n<x> & \"y\"", 300)
    assert "\n" not in out
    assert "&lt;x&gt;" in out
    assert "&amp;" in out
    assert "第一步" in out

    long = ivh_client.normalize_ssml("解" * 500, 280)
    assert len(long) <= 280
    assert long.endswith("...")


def test_normalize_ssml_truncation_never_splits_entity():
    """转义后截断不能把 &amp; 之类的实体拦腰切断（否则 SSML 解析失败）。"""
    out = ivh_client.normalize_ssml("A" * 40 + "&" * 100, 60)
    assert len(out) <= 60
    assert out.endswith("...")
    head = out[:-3]
    amp = head.rfind("&")
    assert amp == -1 or ";" in head[amp:]


# ---------------------------------------------------------------- 响应校验

def test_check_header_rejects_error_code():
    with pytest.raises(ivh_client.IVHError) as ei:
        ivh_client._check_header(
            {"Header": {"Code": 100001, "Message": "Speed value invalid"}}, "提交视频制作"
        )
    assert "100001" in str(ei.value)
    assert not ei.value.retryable


def test_check_header_requires_payload():
    with pytest.raises(ivh_client.IVHError):
        ivh_client._check_header({"Header": {"Code": 0}}, "查询进度")


# ---------------------------------------------------------------- 提交任务

class _FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.text = json.dumps(data, ensure_ascii=False)

    def json(self):
        return self._data


class _FakeClient:
    """按队列返回响应；记录每次请求以便断言 Header/Payload 结构。"""
    responses = []
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeClient.calls.append((url, json, headers))
        item = _FakeClient.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def put(self, url, content=None, headers=None):
        _FakeClient.calls.append(("PUT", url, len(content or b"")))
        return _FakeResponse({}, 200)


def _use_fake_client(monkeypatch, *responses):
    _FakeClient.responses = list(responses)
    _FakeClient.calls = []
    monkeypatch.setattr(ivh_client.httpx, "AsyncClient", _FakeClient)


def _ok(payload):
    return _FakeResponse({"Header": {"Code": 0, "Message": ""}, "Payload": payload})


def test_submit_text_driver_payload_shape(monkeypatch):
    settings.ivh_appkey = "ak"
    settings.ivh_accesstoken = "tk"
    settings.ivh_ref_photo_url = "https://cos.example/teacher.png"
    settings.ivh_driver_type = "Text"
    _use_fake_client(monkeypatch, _ok({"TaskId": "task-1"}))

    task_id = run(
        ivh_client.submit_photo_video(
            "第一步：移项\n2x=4",
            ref_photo_url=settings.ivh_ref_photo_url,
            driver_type="Text",
        )
    )
    assert task_id == "task-1"
    url, body, headers = _FakeClient.calls[0]
    assert "phototovideonotrain?" in url
    assert "signature=" in url
    assert headers["Content-Type"] == "application/json;charset=utf-8"
    assert set(body.keys()) == {"Header", "Payload"}
    p = body["Payload"]
    assert p["DriverType"] == "Text"
    assert p["SpeechParam"]["TimbreKey"] == settings.ivh_timbre_key
    assert "InputSsml" in p and "\n" not in p["InputSsml"]
    assert "InputAudioUrl" not in p


def test_submit_original_voice_uses_audio_url(monkeypatch):
    settings.ivh_appkey = "ak"
    settings.ivh_accesstoken = "tk"
    _use_fake_client(monkeypatch, _ok({"TaskId": "task-2"}))

    run(ivh_client.submit_photo_video(
        "讲解", ref_photo_url="https://cos.example/a.png",
        driver_type="OriginalVoice", audio_url="https://cos.example/a.mp3",
    ))
    p = _FakeClient.calls[0][1]["Payload"]
    assert p["DriverType"] == "OriginalVoice"
    assert p["InputAudioUrl"] == "https://cos.example/a.mp3"
    assert "InputSsml" not in p
    assert "SpeechParam" not in p


def test_submit_rejects_missing_photo_url():
    with pytest.raises(ivh_client.IVHError):
        run(ivh_client.submit_photo_video("讲解", ref_photo_url=""))


def test_submit_network_error_is_retryable(monkeypatch):
    settings.ivh_appkey = "ak"
    settings.ivh_accesstoken = "tk"
    _use_fake_client(monkeypatch, httpx.ConnectError("boom"))
    with pytest.raises(ivh_client.IVHError) as ei:
        run(ivh_client.submit_photo_video(
            "第一步移项，得到二倍未知数等于四", ref_photo_url="https://cos.example/a.png"
        ))
    assert ei.value.retryable


# ---------------------------------------------------------------- 轮询

def test_wait_for_media_success(monkeypatch):
    settings.ivh_appkey = "ak"
    settings.ivh_accesstoken = "tk"
    _use_fake_client(monkeypatch, _ok({
        "Status": "SUCCESS", "MediaUrl": "https://ivh.example/out.mp4",
        "SubtitlesUrl": "", "Progress": 100, "Duration": 11810,
    }))
    media, _sub, ms = run(ivh_client.wait_for_media("task-x"))
    assert media == "https://ivh.example/out.mp4"
    assert ms == 11810
    assert _FakeClient.calls[0][1]["Payload"] == {"TaskId": "task-x"}
    assert "getprogress?" in _FakeClient.calls[0][0]


def test_wait_for_media_fail(monkeypatch):
    settings.ivh_appkey = "ak"
    settings.ivh_accesstoken = "tk"
    _use_fake_client(monkeypatch, _ok({"Status": "FAIL", "FailMessage": "render error"}))
    with pytest.raises(ivh_client.IVHError) as ei:
        run(ivh_client.wait_for_media("task-y"))
    assert "失败" in str(ei.value)


def test_wait_for_media_timeout(monkeypatch):
    settings.ivh_appkey = "ak"
    settings.ivh_accesstoken = "tk"
    settings.ivh_poll_timeout = 1.0
    settings.ivh_poll_interval = 1.0
    _use_fake_client(monkeypatch, _ok({"Status": "MAKING"}))
    with pytest.raises(ivh_client.IVHError) as ei:
        run(ivh_client.wait_for_media("task-z"))
    assert "超时" in str(ei.value)


# ---------------------------------------------------------------- get_generator

def test_get_generator_defaults_to_lite():
    settings.digital_human_enabled = True
    settings.digital_human_impl = "lite"
    assert isinstance(video_generator.get_generator(), video_generator.LiteVideoGenerator)


def test_get_generator_switches_to_ivh():
    settings.digital_human_enabled = True
    settings.digital_human_impl = "ivh"
    assert isinstance(video_generator.get_generator(), video_generator.DigitalHumanGenerator)


def test_get_generator_respects_master_switch():
    settings.digital_human_enabled = False
    settings.digital_human_impl = "ivh"
    assert isinstance(video_generator.get_generator(), video_generator.NoopGenerator)


# ---------------------------------------------------------------- 自动降级

def test_digital_human_falls_back_when_not_configured(tmp_path):
    settings.ivh_appkey = ""
    settings.ivh_accesstoken = ""
    settings.ivh_ref_photo_url = ""
    out = str(tmp_path / "x.mp4")
    with patch.object(
        video_generator.LiteVideoGenerator, "generate",
        return_value=(True, 12.0, None),
    ) as fake, patch.object(
        ivh_client, "submit_photo_video", side_effect=AssertionError("不该调用 IVH")
    ):
        ok, duration, err = run(video_generator.DigitalHumanGenerator().generate("讲解", out))
    assert (ok, duration, err) == (True, 12.0, None)
    fake.assert_awaited_once()


def test_digital_human_falls_back_on_ivh_failure(tmp_path):
    settings.ivh_appkey = "ak"
    settings.ivh_accesstoken = "tk"
    settings.ivh_ref_photo_url = "https://cos.example/a.png"
    out = str(tmp_path / "x.mp4")
    with patch.object(
        ivh_client, "submit_photo_video",
        side_effect=ivh_client.IVHError("鉴权失败"),
    ), patch.object(
        video_generator.LiteVideoGenerator, "generate",
        return_value=(True, 8.0, None),
    ) as fake:
        ok, duration, err = run(video_generator.DigitalHumanGenerator().generate("讲解", out))
    assert (ok, duration, err) == (True, 8.0, None)
    fake.assert_awaited_once()


def test_digital_human_success_path_downloads_and_probes(tmp_path):
    settings.ivh_appkey = "ak"
    settings.ivh_accesstoken = "tk"
    settings.ivh_ref_photo_url = "https://cos.example/a.png"
    settings.ivh_driver_type = "Text"
    settings.ivh_normalize_video = False  # 跳过本地可能缺失的 ffmpeg
    out = str(tmp_path / "x.mp4")

    async def fake_download(url, path):
        with open(path, "wb") as f:
            f.write(b"fake-mp4")

    async def fake_probe(path):
        return 15.5

    with patch.object(ivh_client, "submit_photo_video", return_value="t1"), patch.object(
        ivh_client, "wait_for_media", return_value=("https://ivh.example/o.mp4", None, 15500)
    ), patch.object(ivh_client, "download_video", side_effect=fake_download), patch.object(
        video_generator.LiteVideoGenerator, "_probe_duration", side_effect=fake_probe
    ), patch.object(
        video_generator.LiteVideoGenerator, "generate",
        side_effect=AssertionError("成功路径不该降级"),
    ):
        ok, duration, err = run(video_generator.DigitalHumanGenerator().generate("讲解", out))
    assert (ok, duration, err) == (True, 15.5, None)
    assert os.path.exists(out)
