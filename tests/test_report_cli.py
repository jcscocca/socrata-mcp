from socrata_mcp import report_cli
from socrata_mcp.errors import PortalError


class StubProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def generate_report(self, domain, dataset_id, out_path, where=None, title=None):
        self.calls.append(
            {"domain": domain, "dataset_id": dataset_id, "out_path": out_path,
             "where": where, "title": title}
        )
        if self.error:
            raise self.error
        return self.result


def install(monkeypatch, stub):
    monkeypatch.setattr(report_cli, "_make_provider", lambda: stub)


def test_happy_path_prints_path_and_notes(monkeypatch, capsys, tmp_path):
    out = tmp_path / "r.html"
    stub = StubProvider(
        result={"path": str(out), "sections": ["quality"], "notes": ["n1"], "queries": []}
    )
    install(monkeypatch, stub)
    code = report_cli.main(
        ["data.example.gov", "abcd-1234", "-o", str(out),
         "--where", "a > 1", "--title", "T"]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip() == str(out)
    assert "note: n1" in captured.err
    assert stub.calls[0]["where"] == "a > 1"
    assert stub.calls[0]["title"] == "T"


def test_default_out_path(monkeypatch):
    stub = StubProvider(
        result={"path": "abcd-1234-report.html", "sections": [], "notes": [], "queries": []}
    )
    install(monkeypatch, stub)
    assert report_cli.main(["data.example.gov", "abcd-1234"]) == 0
    assert stub.calls[0]["out_path"] == "abcd-1234-report.html"


def test_error_exits_nonzero(monkeypatch, capsys):
    install(monkeypatch, StubProvider(error=PortalError("no such view", status=404)))
    code = report_cli.main(["data.example.gov", "nope-0000"])
    assert code == 1
    assert "no such view" in capsys.readouterr().err


def test_write_error_exits_nonzero(monkeypatch, capsys):
    install(monkeypatch, StubProvider(error=PermissionError("denied")))
    code = report_cli.main(["data.example.gov", "abcd-1234", "-o", "/nope/r.html"])
    assert code == 1
    assert "denied" in capsys.readouterr().err


def test_keyboard_interrupt_exits_130(monkeypatch, capsys):
    install(monkeypatch, StubProvider(error=KeyboardInterrupt()))
    code = report_cli.main(["data.example.gov", "abcd-1234"])
    assert code == 130
    assert "interrupted" in capsys.readouterr().err
