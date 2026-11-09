import json
from pathlib import Path

import pytest

from ingest.chunk import chunk_sentences, main
from ingest.clean import Sentence, clean_vtt, load_sentences, parse_cues, plain_text

FIXTURE = Path(__file__).parent / "fixtures" / "sample.en.vtt"

# YouTube's rolling-caption layout. {sp} marks the whitespace-only lines YouTube puts
# inside cues (written as a placeholder so editors don't strip them).
ROLLING_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.500 --> 00:00:02.000 align:start position:0%
{sp}
hello<00:00:00.800><c> everyone</c><00:00:01.200><c> welcome</c>

00:00:02.000 --> 00:00:02.010 align:start position:0%
hello everyone welcome
{sp}

00:00:02.010 --> 00:00:04.000 align:start position:0%
hello everyone welcome
to<00:00:02.300><c> the</c><00:00:02.600><c> Q&amp;A.</c>

00:00:04.000 --> 00:00:04.010 align:start position:0%
to the Q&amp;A.
{sp}

00:00:04.010 --> 00:00:06.000 align:start position:0%
to the Q&amp;A.
&gt;&gt; Yeah.

00:00:06.000 --> 00:00:06.010 align:start position:0%
&gt;&gt; Yeah.
{sp}

00:00:06.010 --> 00:00:08.000 align:start position:0%
&gt;&gt; Yeah.
&gt;&gt; Yeah.

00:00:08.000 --> 00:00:09.000 align:start position:0%
&gt;&gt; Yeah.
[applause]<00:00:08.500><c> so</c>

00:00:09.000 --> 00:00:10.000 align:start position:0%
[applause] so
[clears<00:00:09.100><c> throat]</c><00:00:09.400><c> next</c>
"""


@pytest.fixture(scope="module")
def fixture_sentences() -> list[Sentence]:
    return load_sentences(FIXTURE)


@pytest.mark.parametrize("space", [" ", ""], ids=["youtube", "whitespace-stripped"])
def test_rolling_repeats_dropped_and_markup_removed(space: str) -> None:
    sentences = clean_vtt(ROLLING_VTT.replace("{sp}", space))
    texts = [s.text for s in sentences]
    assert texts == ["hello everyone welcome to the Q&A.", "Yeah.", "Yeah.", "so next"]
    # first word starts at its cue; later sentences at their first word's inline timestamp
    assert [s.start_sec for s in sentences] == [0.5, 4.01, 6.01, 8.5]


def test_pause_and_length_split_unpunctuated_words() -> None:
    cue = "<00:00:{:06.3f}><c> w{}</c>"
    words = "".join(cue.format(1 + 0.3 * i, i) for i in range(1, 60))
    words += "<00:00:40.000><c> after</c><00:00:40.300><c> pause</c>"
    vtt = f"WEBVTT\n\n00:00:01.000 --> 00:00:45.000\nw0{words}\n"
    sentences = clean_vtt(vtt)
    assert all(len(s.text.split()) <= 25 for s in sentences)
    assert sentences[-1].text == "after pause"
    assert sentences[-1].start_sec == 40.0


def test_fixture_has_no_repeated_lines(fixture_sentences: list[Sentence]) -> None:
    assert len(fixture_sentences) > 30
    text = " ".join(s.text for s in fixture_sentences)
    caption_lines = {
        plain_text(line) for cue in parse_cues(FIXTURE.read_text()) for line in cue.lines
    }
    long_lines = [line for line in caption_lines if len(line.split()) >= 4]
    assert len(long_lines) > 50
    for line in long_lines:
        assert f"{line} {line}" not in text, line
    for previous, sentence in zip(fixture_sentences, fixture_sentences[1:], strict=False):
        assert sentence.text != previous.text
    assert "<" not in text and "&gt;" not in text and "&amp;" not in text


def test_fixture_sentence_times(fixture_sentences: list[Sentence]) -> None:
    assert fixture_sentences[0].start_sec == pytest.approx(0.56)
    assert fixture_sentences[0].text.startswith("All right, thanks for")
    starts = [s.start_sec for s in fixture_sentences]
    assert starts == sorted(starts)
    assert all(s.end_sec >= s.start_sec for s in fixture_sentences)


def test_fixture_chunks(fixture_sentences: list[Sentence]) -> None:
    chunks = chunk_sentences(fixture_sentences)
    assert len(chunks) >= 3
    for chunk in chunks:
        assert isinstance(chunk.start_sec, int) and isinstance(chunk.end_sec, int)
        assert 45 <= chunk.end_sec - chunk.start_sec <= 100
    for previous, chunk in zip(chunks, chunks[1:], strict=False):
        assert chunk.start_sec > previous.start_sec
        assert 0 < previous.end_sec - chunk.start_sec <= 40  # ~15 s overlap
    # chunks start on sentence starts and cover the whole talk
    sentence_starts = {int(s.start_sec) for s in fixture_sentences}
    assert {c.start_sec for c in chunks} <= sentence_starts
    assert chunks[0].start_sec == 0
    assert chunks[-1].end_sec >= int(fixture_sentences[-1].end_sec)
    assert chunks[-1].text.endswith(fixture_sentences[-1].text)


def test_long_caption_gap_starts_new_segment() -> None:
    sentences = [Sentence(float(t), float(t) + 4.0, f"s{t}") for t in range(0, 120, 5)]
    sentences += [Sentence(float(t), float(t) + 4.0, f"s{t}") for t in range(300, 420, 5)]
    chunks = chunk_sentences(sentences)
    assert all(45 <= c.end_sec - c.start_sec <= 100 for c in chunks)
    assert not any(c.start_sec < 300 < c.end_sec for c in chunks)


def test_short_caption_segment_after_gap_joins_previous() -> None:
    sentences = [Sentence(float(t), float(t) + 4.0, f"s{t}") for t in range(0, 45, 5)]
    sentences += [Sentence(float(t), float(t) + 4.0, f"s{t}") for t in range(80, 90, 5)]

    chunks = chunk_sentences(sentences)

    assert len(chunks) == 1
    assert chunks[0].start_sec == 0
    assert chunks[0].end_sec == 89
    assert chunks[0].text.endswith("s85")


def test_short_caption_segment_after_long_segment_does_not_join() -> None:
    sentences = [Sentence(float(t), float(t) + 4.0, f"s{t}") for t in range(0, 240, 5)]
    sentences += [Sentence(float(t), float(t) + 4.0, f"s{t}") for t in range(300, 310, 5)]

    chunks = chunk_sentences(sentences)

    assert chunks[-1].start_sec == 300
    assert chunks[-1].end_sec == 309
    assert not any(chunk.start_sec < 300 < chunk.end_sec for chunk in chunks)


def test_cli_writes_contract_records(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "abc123.en.vtt").write_text(FIXTURE.read_text())
    talks = tmp_path / "talks.csv"
    talks.write_text(
        "youtube_id,title,speakers,track\n"
        'abc123,Talk title,"Ada Lovelace; Grace Hopper",Observability\n'
        "nocaps1,No captions,Someone,Security\n"
    )
    out = tmp_path / "out" / "chunks.jsonl"
    assert main(["--talks", str(talks), "--raw-dir", str(raw), "--out", str(out)]) == 0

    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(records) >= 3
    first = records[0]
    assert list(first) == [
        "chunk_id",
        "talk_id",
        "youtube_id",
        "title",
        "speakers",
        "track",
        "start_sec",
        "end_sec",
        "text",
        "tools",
        "talk_label",
    ]
    assert first["chunk_id"] == f"abc123-{first['start_sec']}"
    assert first["talk_id"] == first["youtube_id"] == "abc123"
    assert first["speakers"] == ["Ada Lovelace", "Grace Hopper"]
    assert first["track"] == "Observability"
    assert first["tools"] == [] and first["talk_label"] is None
    assert len({r["chunk_id"] for r in records}) == len(records)

    printed = capsys.readouterr().out
    assert "talks processed:  1" in printed
    assert "nocaps1" in printed
