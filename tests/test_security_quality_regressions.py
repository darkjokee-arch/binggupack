import builtins

from binggupack.pack import branch_explorer
from binggupack.pack.incoming_to_staging import scan_secrets
from binggupack.studio import server as studio_server
from scripts import binggu_discover, binggu_setup_save
from scripts import openbinggu_incoming_to_staging as incoming_wrapper


def test_setup_report_never_reveals_connector_token(tmp_path):
    fixture_value = "secret-" + "token-value-123456789"
    binggu_setup_save.write_dev_vars(
        str(tmp_path / binggu_setup_save.VARS_NAME),
        {"WORKER_URL": "https://example.workers.dev", "SAVE_PATH_TOKEN": fixture_value},
    )

    result = binggu_setup_save.connector_step(str(tmp_path), show_url=True)

    assert fixture_value not in result["msg"]
    assert fixture_value not in (result.get("hint") or "")


def test_secret_runner_echo_is_never_returned_in_report():
    fixture_value = "secret-" + "runner-echo-123456789"

    def echoing_failure(_args, _cwd, _input):
        return {"rc": 1, "stdout": fixture_value, "stderr": "failed: " + fixture_value}

    result = binggu_setup_save.secrets_put_step(
        {key: fixture_value for key in binggu_setup_save.KEY_FIELDS},
        secret_runner=echoing_failure,
        apply=True,
        deploy=True,
    )

    assert fixture_value not in str(result)


def test_secret_scan_result_contains_no_secret_prefix():
    fixture_value = "ghp_" + "Abc123SecretValue987654321"
    hits = scan_secrets({"items": [{"item_id": "one", "text": fixture_value}]})

    assert hits
    assert fixture_value[:8] not in str(hits)
    assert fixture_value not in str(hits)


def test_discover_kind_uses_hostname_boundaries():
    assert binggu_discover.infer_kind("https://github.com/org/repo") == "github"
    assert binggu_discover.infer_kind("https://docs.github.com/page") == "github"
    assert binggu_discover.infer_kind("https://evilgithub.com/repo") == "url"
    assert binggu_discover.infer_kind("https://github.com.evil/repo") == "url"
    assert binggu_discover.infer_kind("https://export.arxiv.org/abs/1") == "arxiv"
    assert binggu_discover.infer_kind("https://arxiv.org.evil/abs/1") == "url"


def test_control_character_filter_matches_only_intended_boundaries():
    for codepoint in (*range(0x00, 0x09), 0x0B, 0x0C, *range(0x0E, 0x20), 0xFFFD):
        assert branch_explorer._CONTROL_RE.search(chr(codepoint))
    for text in ("\t", "\n", "\r", "A", "한"):
        assert branch_explorer._CONTROL_RE.search(text) is None


def test_static_reader_rejects_unlisted_paths_before_open(monkeypatch):
    calls = []

    def forbidden_open(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("unlisted path reached open")

    monkeypatch.setattr(studio_server, "_res_files", None)
    monkeypatch.setattr(builtins, "open", forbidden_open)
    for name in ("../index.html", "/etc/passwd", "%2e%2e/index.html", "index.html/../../x"):
        assert studio_server._read_static(name) is None
    assert calls == []


def test_incoming_wrapper_preserves_legacy_exports():
    for name in (
        "SECRET_PATTERNS", "scan_secrets", "assess_incoming", "_expected_from_name",
        "_base_pack", "_content", "synthesize_fixtures", "v010",
    ):
        assert hasattr(incoming_wrapper, name)
