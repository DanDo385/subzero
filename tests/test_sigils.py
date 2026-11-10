from subzero.sigils import parse, parse_all


def test_project_sigil_sets_home_and_slug() -> None:
    s = parse("%Code/portfolio-site add a dark mode toggle")
    assert s.project == "portfolio-site"
    assert s.home == "projects/portfolio-site"


def test_lesson_topic_becomes_a_concept() -> None:
    s = parse("^cfa2/fixed-income duration vs convexity")
    assert s.home == "lesson/cfa2"
    assert s.lesson == "cfa2"
    assert s.concepts == ("fixed-income",)


def test_first_home_sigil_wins_others_still_tag() -> None:
    s = parse("&tailscale then %Code/subzero #acl @someone #ACL")
    assert s.home == "setup/tailscale"
    assert s.project == "subzero"  # still read, for project resolution
    assert s.concepts == ("acl",)
    assert s.people == ("someone",)
    assert s.tags() == ["setup/tailscale", "concept/acl", "person/someone"]


def test_targets_are_collected() -> None:
    s = parse(">grok find who is talking about zig 0.16 >claude")
    assert s.targets == ("grok", "claude")


def test_ordinary_text_is_left_alone() -> None:
    s = parse("C# and x^2 cost 50% at a@b.com, see https://x.dev/#top and 3>2")
    assert not s.any
    assert s.home is None and s.concepts == () and s.people == ()


def test_markdown_heading_is_not_a_concept() -> None:
    assert parse("# Heading\n## Another").concepts == ()


def test_trailing_punctuation_is_dropped() -> None:
    s = parse("Working on %Code/portfolio-site. Also #comptime.")
    assert s.project == "portfolio-site"
    assert s.concepts == ("comptime",)


def test_plain_text_keeps_words_for_search() -> None:
    assert parse("^cfa2/fixed-income #duration").plain == "cfa2 fixed income duration"


def test_parse_all_reads_title_before_summary() -> None:
    s = parse_all("^zig session", "later moved to %Code/tools")
    assert s.home == "lesson/zig"


def test_empty_input() -> None:
    assert not parse(None).any
    assert not parse("").any


def test_code_and_keystrokes_are_not_sigils() -> None:
    for text in ('printf("%s\\n")', "press ^C to stop", "50 %20 off", "if x >2", "@a"):
        s = parse(text)
        assert s.home is None and s.targets == () and s.people == (), text


def test_brackets_still_open_a_sigil() -> None:
    assert parse("(%Code/site) fix").project == "site"


def test_concepts_may_start_with_a_digit() -> None:
    assert parse("#401k plan").concepts == ("401k",)
