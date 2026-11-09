from pathlib import Path

import pytest

from ingest import download
from ingest.download import Talk, caption_path, download_all, read_talks

CSV = (
    "youtube_id,title,speakers,track\r\n"
    'aaa111,"Backend-First IDP: Argo CD, Crossplane, and OPA",Abhinav Sharma; Aditya Soni,'
    "Platform Engineering Day\r\n"
    "bbb222,Prometheus V3 One Year In,Jan Fajerski; Bartłomiej Płotka,\r\n"
    "ccc333,Solo Talk,Regina Voloshin,ArgoCon\r\n"
)


def write_csv(tmp_path: Path, text: str = CSV) -> Path:
    path = tmp_path / "talks.csv"
    path.write_text(text, encoding="utf-8")
    return path


def talk(youtube_id: str) -> Talk:
    return Talk(youtube_id=youtube_id, title=f"Talk {youtube_id}", speakers=(), track="")


def fake_writer(calls: list[str]):
    """A fetcher that writes the caption file, like yt-dlp would."""

    def fetch(youtube_id: str, raw_dir: Path) -> None:
        calls.append(youtube_id)
        caption_path(raw_dir, youtube_id).write_text("WEBVTT\n", encoding="utf-8")

    return fetch


def test_read_talks_handles_quoted_commas_and_split_speakers(tmp_path):
    talks = read_talks(write_csv(tmp_path))

    assert [t.youtube_id for t in talks] == ["aaa111", "bbb222", "ccc333"]
    assert talks[0].title == "Backend-First IDP: Argo CD, Crossplane, and OPA"
    assert talks[0].speakers == ("Abhinav Sharma", "Aditya Soni")
    assert talks[0].track == "Platform Engineering Day"
    assert talks[1].speakers == ("Jan Fajerski", "Bartłomiej Płotka")
    assert talks[1].track == ""
    assert talks[2].speakers == ("Regina Voloshin",)


def test_committed_talks_csv_parses():
    talks = read_talks(Path(__file__).resolve().parents[1] / "data" / "talks.csv")

    assert 40 <= len(talks) <= 60
    assert len({t.youtube_id for t in talks}) == len(talks)
    assert all(t.title and t.speakers for t in talks)


def test_existing_files_are_skipped(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    caption_path(raw, "aaa111").write_text("WEBVTT\n", encoding="utf-8")
    calls: list[str] = []

    report = download_all([talk("aaa111"), talk("bbb222")], raw, fetch=fake_writer(calls))

    assert calls == ["bbb222"]
    assert report.present == ["aaa111"]
    assert report.fetched == ["bbb222"]
    assert report.failed == []


def test_failures_are_listed_and_batch_continues(tmp_path):
    calls: list[str] = []
    writer = fake_writer(calls)

    def fetch(youtube_id: str, raw_dir: Path) -> None:
        if youtube_id == "boom":
            raise RuntimeError("ERROR: [youtube] boom: HTTP Error 429: Too Many Requests")
        if youtube_id == "nosubs":
            return  # yt-dlp succeeds but writes nothing when there are no English captions
        writer(youtube_id, raw_dir)

    talks = [talk("boom"), talk("nosubs"), talk("ok")]
    report = download_all(talks, tmp_path / "raw", fetch=fetch)

    assert report.fetched == ["ok"]
    assert report.failed == [
        ("boom", "[youtube] boom: HTTP Error 429: Too Many Requests"),
        ("nosubs", "no English automatic captions"),
    ]


def test_main_exit_code_and_failure_list(tmp_path, monkeypatch, capsys):
    raw = tmp_path / "raw"

    def fetch(youtube_id: str, raw_dir: Path) -> None:
        if youtube_id == "bbb222":
            raise RuntimeError("Video unavailable")
        caption_path(raw_dir, youtube_id).write_text("WEBVTT\n", encoding="utf-8")

    monkeypatch.setattr(download, "fetch_captions", fetch)
    argv = ["--talks", str(write_csv(tmp_path)), "--raw-dir", str(raw)]

    assert download.main(argv) == 1
    out = capsys.readouterr().out
    assert "2/3 caption files present" in out
    assert "1 failed" in out
    assert "bbb222" in out and "Video unavailable" in out

    # Second run: the two fetched files are skipped; fix the failing one and all pass.
    monkeypatch.setattr(download, "fetch_captions", fake_writer(calls := []))
    assert download.main(argv) == 0
    assert calls == ["bbb222"]
    assert "3/3 caption files present" in capsys.readouterr().out


def test_main_never_hits_network_when_all_present(tmp_path, monkeypatch, capsys):
    raw = tmp_path / "raw"
    raw.mkdir()
    for youtube_id in ("aaa111", "bbb222", "ccc333"):
        caption_path(raw, youtube_id).write_text("WEBVTT\n", encoding="utf-8")

    def no_network(youtube_id: str, raw_dir: Path) -> None:
        pytest.fail(f"fetched {youtube_id} although its file exists")

    monkeypatch.setattr(download, "fetch_captions", no_network)
    argv = ["--talks", str(write_csv(tmp_path)), "--raw-dir", str(raw)]

    assert download.main(argv) == 0
    assert "3/3 caption files present (3 already there, 0 fetched), 0 failed" in (
        capsys.readouterr().out
    )


def test_ydl_options_are_captions_only_and_polite(tmp_path):
    opts = download.ydl_options(tmp_path)

    assert opts["skip_download"] is True
    assert opts["writeautomaticsub"] is True
    assert opts["subtitleslangs"] == ["en"]
    assert opts["subtitlesformat"] == "vtt"
    assert opts["sleep_interval_requests"] == 3
    assert opts["sleep_interval_subtitles"] == 6
    assert opts["sleep_interval"] == 4
    assert opts["max_sleep_interval"] == 8
