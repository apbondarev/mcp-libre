"""Tests for hyperlinks: listing them, and taking one away.

Measured on a real document: a link is HyperLinkURL on the run, its look
comes from the UnvisitedCharStyleName / VisitedCharStyleName character
styles rather than from a colour, and clearing the URL alone leaves that look
behind. A link into the same document can be checked; one pointing outside
cannot be, from a server that never reaches the network.
"""

import pytest

from tests.fake_writer import writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(["Scalar types",
                       "See the GraphQL specification for more.",
                       "And the section above, and a dead end."],
                      outline_levels=[1, 0, 0], caret=(1, 0))


def link_up(bridge, doc):
    bridge.format_range({"paragraph": 1, "offset": 8, "length": 21},
                        link="https://spec.graphql.org/", doc=doc)
    bridge.add_bookmark({"paragraph": 0}, "Скаляры", doc=doc)
    bridge.format_range({"paragraph": 2, "offset": 8, "length": 13},
                        link="#Скаляры", doc=doc)
    bridge.format_range({"paragraph": 2, "offset": 27, "length": 8},
                        link="#Нетакой", doc=doc)


def test_format_range_can_make_a_link_without_rewriting_the_text(bridge, doc):
    made = bridge.format_range({"paragraph": 1, "offset": 8, "length": 21},
                               link="https://spec.graphql.org/", doc=doc)

    assert made["success"] is True
    assert doc.getText().paragraphs[1] == \
        "See the GraphQL specification for more."
    listed = bridge.list_hyperlinks(doc=doc)
    assert listed["count"] == 1
    assert listed["links"][0]["text"] == "GraphQL specification"


def test_listing_says_what_each_link_points_at(bridge, doc):
    link_up(bridge, doc)

    listed = bridge.list_hyperlinks(doc=doc)

    assert (listed["count"], listed["distinct_urls"]) == (3, 3)
    assert listed["internal"] == 2
    external = [one for one in listed["links"] if not one["internal"]]
    # Nothing here reaches the network, so an http link is neither claimed
    # sound nor claimed broken.
    assert [one["broken"] for one in external] == [None]


def test_an_internal_link_is_checked_against_the_document(bridge, doc):
    link_up(bridge, doc)

    listed = bridge.list_hyperlinks(doc=doc)

    by_url = {one["url"]: one for one in listed["links"]}
    assert by_url["#Скаляры"]["broken"] is False
    assert by_url["#Нетакой"]["broken"] is True
    assert listed["broken"] == 1


def test_a_link_can_point_at_a_heading_by_its_words(bridge, doc):
    bridge.format_range({"paragraph": 2, "offset": 8, "length": 13},
                        link="#Scalar types", doc=doc)

    listed = bridge.list_hyperlinks(doc=doc)

    assert listed["links"][0]["broken"] is False


def test_a_link_split_into_runs_is_still_one_link(bridge, doc):
    link_up(bridge, doc)

    bridge.format_range({"paragraph": 1, "offset": 8, "length": 7}, bold=True,
                        doc=doc)

    runs = bridge.read_runs({"paragraph": 1}, doc=doc)
    assert len([run for run in runs["runs"] if run.get("link")]) == 2
    listed = bridge.list_hyperlinks({"paragraph": 1}, doc=doc)
    assert listed["count"] == 1
    assert listed["links"][0]["text"] == "GraphQL specification"


def test_removing_a_link_keeps_the_words_and_drops_the_look(bridge, doc):
    link_up(bridge, doc)

    gone = bridge.remove_hyperlink({"paragraph": 1}, doc=doc)

    assert (gone["success"], gone["removed"]) == (True, 1)
    assert doc.getText().paragraphs[1] == \
        "See the GraphQL specification for more."
    runs = bridge.read_runs({"paragraph": 1}, doc=doc)
    assert [run for run in runs["runs"] if run.get("link")] == []
    assert [run for run in runs["runs"]
            if run.get("character_style")] == [], "the link style stayed"
    assert bridge.list_hyperlinks(doc=doc)["count"] == 2


def test_removing_by_url_and_removing_the_lot(bridge, doc):
    link_up(bridge, doc)

    assert bridge.remove_hyperlink(url="#Нетакой", doc=doc)["removed"] == 1
    assert bridge.list_hyperlinks(doc=doc)["broken"] == 0
    assert bridge.remove_hyperlink(all=True, doc=doc)["removed"] == 2
    assert bridge.list_hyperlinks(doc=doc)["count"] == 0


def test_the_links_are_picked_in_exactly_one_way(bridge, doc):
    link_up(bridge, doc)

    for refused in (bridge.remove_hyperlink(doc=doc),
                    bridge.remove_hyperlink(url="x", all=True, doc=doc),
                    bridge.remove_hyperlink(address={"paragraph": 1}, all=True,
                                            doc=doc)):
        assert (refused["success"], refused["code"]) == (False,
                                                         "INVALID_PARAMETER")
    assert bridge.remove_hyperlink(url="https://nowhere/",
                                   doc=doc)["code"] == "NOT_FOUND"
    assert bridge.remove_hyperlink(address={"paragraph": 0},
                                   doc=doc)["code"] == "NOT_FOUND"


def test_a_scoped_listing_answers_about_that_scope_alone(bridge, doc):
    link_up(bridge, doc)

    # And it does not read the rest of the document to do it: on a real
    # document the whole-body walk cost a second and a half, and asking
    # about one paragraph now costs a hundredth of it.
    listed = bridge.list_hyperlinks({"paragraph": 2}, doc=doc)

    assert listed["count"] == 2
    # Each link is named by an anchor over the portions it spans: working out
    # a paragraph number means walking the body to it.
    assert all(one["address"]["anchor"]["type"] == "text"
               for one in listed["links"])
    assert [bridge._resolve_address(doc, one["address"]).getString()
            for one in listed["links"]] == [one["text"]
                                            for one in listed["links"]]
    assert all(one["address"]["paragraph"] == 2 for one
               in bridge.list_hyperlinks({"paragraph": 2}, number=True,
                                         doc=doc)["links"])


def test_writers_own_heading_mark_is_not_called_broken(bridge, doc):
    # Measured on a real guide: every one of its 256 table-of-contents links
    # points at a __RefHeading__ mark, and those are in none of the
    # collections UNO offers — not the bookmarks, not the reference marks —
    # so the whole contents was reported broken.
    bridge.format_range({"paragraph": 1, "offset": 0, "length": 5},
                        link="#__RefHeading__5636_1566568644", doc=doc)

    one, = [link for link in bridge.list_hyperlinks({"paragraph": 1},
                                                    doc=doc)["links"]
            if link["url"].startswith("#__RefHeading__")]

    assert one["internal"] is True
    assert one["broken"] is None
    assert "unknown" in one["note"]


def test_a_name_with_a_space_in_it_is_read_as_a_name(bridge, doc):
    # An internal link carries its target as a URL, so a space arrives as
    # %20 — and comparing that with the bookmark's name called it broken.
    bridge.add_bookmark({"paragraph": 2}, "конец раздела", doc=doc)
    bridge.format_range({"paragraph": 1, "offset": 0, "length": 5},
                        link="#%D0%BA%D0%BE%D0%BD%D0%B5%D1%86%20%D1%80%D0%B0"
                             "%D0%B7%D0%B4%D0%B5%D0%BB%D0%B0", doc=doc)

    one, = [link for link in bridge.list_hyperlinks({"paragraph": 1},
                                                    doc=doc)["links"]
            if link["url"].startswith("#%")]

    assert one["points_at"] == "конец раздела"
    assert one["broken"] is False


def test_a_scoped_listing_never_walks_to_its_paragraphs(bridge, doc,
                                                        monkeypatch):
    # The scope used to be a predicate on numbers: the address was numbered,
    # then the body walked from its start until that number came up — 4.5s on
    # a real guide for a selection two thirds of the way through it.
    link_up(bridge, doc)

    def refuse(*arguments, **named):
        raise AssertionError("a scoped listing walked the body")

    monkeypatch.setattr(bridge, "_body_paragraphs_only", refuse)
    monkeypatch.setattr(bridge, "_locate_paragraph", refuse)

    listed = bridge.list_hyperlinks({"paragraph": 2}, doc=doc)

    assert listed["count"] == 2


def test_a_block_of_paragraphs_scopes_to_all_of_them(bridge, doc):
    link_up(bridge, doc)

    listed = bridge.list_hyperlinks({"paragraph": 1, "through": 2}, doc=doc)

    assert listed["count"] == 3
    assert listed["scope"] == {"paragraph": 1, "through": 2}
    assert bridge.list_hyperlinks({"paragraph": 1, "through": 2}, number=True,
                                  doc=doc)["scope"] == {"paragraphs": [1, 2]}
