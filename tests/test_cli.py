import pytest

from aax2text import cli


def test_parse_formats_default():
    assert cli._parse_formats("txt") == ("txt",)
    assert cli._parse_formats("txt,srt,json") == ("txt", "srt", "json")


def test_parse_formats_rejects_unknown():
    import argparse
    with pytest.raises(argparse.ArgumentTypeError):
        cli._parse_formats("txt,docx")


def test_parser_convert():
    args = cli.build_parser().parse_args(
        ["convert", "book.aax", "--crack", "--model", "medium", "--format", "txt,srt"]
    )
    assert args.command == "convert"
    assert args.input == "book.aax"
    assert args.crack is True
    assert args.model == "medium"
    assert args.format == ("txt", "srt")


def test_parser_crack_with_checksum():
    args = cli.build_parser().parse_args(["crack", "--checksum", "ab" * 20])
    assert args.command == "crack"
    assert args.checksum == "ab" * 20


def test_parser_requires_command():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])
