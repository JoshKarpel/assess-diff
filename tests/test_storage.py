from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from threading import Thread

import pytest

from assess_diff.review import Review
from assess_diff.review import UnreadableReview
from assess_diff.review import edit_summary
from assess_diff.storage import NoSuchReview
from assess_diff.storage import OpenReview
from assess_diff.storage import ReviewChanged
from assess_diff.storage import ReviewStore
from assess_diff.storage import Stored
from assess_diff.storage import digest
from assess_diff.storage import for_project
from assess_diff.storage import reviews_directory
from tests.builders import STARTED
from tests.builders import review
from tests.test_review import V1


@pytest.fixture
def store(tmp_path: Path) -> ReviewStore:
    return ReviewStore(tmp_path / "data" / "reviews")


def named(
    review_id: str, updated: datetime = STARTED, worktree: str = "/work/repo", project: str = "/work/repo/.git"
) -> Review:
    return review().model_copy(
        update={"id": review_id, "updated": updated, "worktree": Path(worktree), "project": Path(project)}
    )


def test_reviews_directory_is_under_the_data_home() -> None:
    assert reviews_directory(Path("/data")) == Path("/data/assess-diff/reviews")


def test_reviews_directory_defaults_to_local_share(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert reviews_directory(None) == tmp_path / ".local" / "share" / "assess-diff" / "reviews"


def test_save_then_load_round_trips(store: ReviewStore) -> None:
    saved = store.save(review(summary="ship it"), None)

    loaded = store.load(saved.review.id)

    assert (loaded.review, loaded.digest) == (saved.review, saved.digest)


def test_save_writes_json_indented_for_reading(store: ReviewStore) -> None:
    saved = store.save(review(summary="for a person to read too"), None)

    text = store.path(saved.review.id).read_text()

    assert text == json.dumps(json.loads(text), indent=2)


def test_save_writes_atomically_leaving_no_temporary_file(store: ReviewStore) -> None:
    store.save(review(), None)

    assert sorted(path.name for path in store.directory.iterdir()) == [".lock", f"{review().id}.json"]


def test_save_waits_for_the_lock_and_then_sees_what_was_written_under_it(store: ReviewStore) -> None:
    first = store.save(review(summary="mine"), None)
    outcome: list[Stored | Exception] = []

    def save_later() -> None:
        try:
            outcome.append(store.save(review(summary="mine, later"), first.digest))
        except ReviewChanged as error:
            outcome.append(error)

    with store.locked():
        saver = Thread(target=save_later)
        saver.start()
        # A save that skipped the lock would finish in well under this; one that takes it can't finish at all.
        saver.join(timeout=0.5)
        assert saver.is_alive()
        store.path(first.review.id).write_text(review(summary="theirs").model_dump_json())
    saver.join()

    assert [type(result) for result in outcome] == [ReviewChanged]
    assert store.load(first.review.id).review.summary == "theirs"


def test_reviews_leaves_out_the_lock_file(store: ReviewStore) -> None:
    store.save(review(), None)

    assert [r.id for r in store.reviews()] == [review().id]


def test_save_over_a_file_changed_by_another_process_refuses(store: ReviewStore) -> None:
    first = store.save(review(summary="mine"), None)
    store.path(first.review.id).write_text(review(summary="theirs").model_dump_json())

    with pytest.raises(ReviewChanged, match=r"^Review 0199aaaa-review was changed by another process$"):
        store.save(review(summary="mine, later"), first.digest)


def test_save_of_a_new_review_over_an_existing_file_refuses(store: ReviewStore) -> None:
    store.save(review(), None)

    with pytest.raises(ReviewChanged):
        store.save(review(), None)


def test_reviews_lists_the_most_recently_updated_first(store: ReviewStore) -> None:
    for review_id, minutes in (("older", 1), ("newest", 3), ("middle", 2)):
        store.save(named(review_id, STARTED + timedelta(minutes=minutes)), None)

    assert [r.id for r in store.reviews()] == ["newest", "middle", "older"]


def test_reviews_of_a_store_never_written_is_none(store: ReviewStore) -> None:
    assert store.reviews() == ()


def test_find_takes_any_unambiguous_prefix(store: ReviewStore) -> None:
    store.save(named("abc123"), None)
    store.save(named("abd456"), None)

    assert store.find("abc").review.id == "abc123"


@pytest.mark.parametrize(
    ("prefix", "message"),
    [("ab", r"^2 reviews with an id starting with 'ab'$"), ("zz", r"^No review with an id starting with 'zz'$")],
)
def test_find_refuses_an_ambiguous_or_unknown_prefix(store: ReviewStore, prefix: str, message: str) -> None:
    store.save(named("abc123"), None)
    store.save(named("abd456"), None)

    with pytest.raises(NoSuchReview, match=message):
        store.find(prefix)


def test_delete_removes_the_file(store: ReviewStore) -> None:
    store.save(named("doomed"), None)

    store.delete("doomed")

    assert store.reviews() == ()


def test_for_project_puts_this_worktree_first_and_leaves_other_projects_out() -> None:
    reviews = (
        named("other-tree", worktree="/work/linked"),
        named("other-project", worktree="/elsewhere", project="/elsewhere/.git"),
        named("this-tree"),
    )

    found = for_project(reviews, Path("/work/repo/.git"), Path("/work/repo"))

    assert [r.id for r in found] == ["this-tree", "other-tree"]


def test_digest_is_the_contents_sha256() -> None:
    assert digest(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


CLOCK = datetime(2026, 5, 5, 5, 5, tzinfo=UTC)


def opened(store: ReviewStore) -> OpenReview:
    return OpenReview(store=store, review=review(), digest=None, new_id=lambda: "forked-id", now=lambda: CLOCK)


def test_update_saves_the_edit_and_stamps_the_time(store: ReviewStore) -> None:
    open_review = opened(store)

    notice = open_review.update(lambda r: edit_summary(r, lambda _: "saved"))

    on_disk = store.load(open_review.review.id)
    assert (notice, on_disk.review.summary, on_disk.review.updated, open_review.digest) == (
        None,
        "saved",
        CLOCK,
        on_disk.digest,
    )


def test_consecutive_updates_each_save_over_the_last(store: ReviewStore) -> None:
    open_review = opened(store)

    open_review.update(lambda r: edit_summary(r, lambda _: "one"))
    open_review.update(lambda r: edit_summary(r, lambda _: "two"))

    assert store.load(open_review.review.id).review.summary == "two"


def test_update_over_another_process_saves_as_a_new_review(store: ReviewStore) -> None:
    open_review = opened(store)
    open_review.update(lambda r: edit_summary(r, lambda _: "mine"))
    store.path(open_review.review.id).write_text(review(summary="theirs").model_dump_json())

    notice = open_review.update(lambda r: edit_summary(r, lambda _: "mine, later"))

    assert notice == "Another assess-diff changed review 0199aaaa-review, so this one is saved as forked-id"
    assert (store.load("0199aaaa-review").review.summary, store.load("forked-id").review.summary) == (
        "theirs",
        "mine, later",
    )
    assert open_review.review.id == "forked-id"


def test_reviews_migrate_a_file_an_older_version_wrote(store: ReviewStore) -> None:
    store.directory.mkdir(parents=True)
    (store.directory / "old.json").write_bytes(V1)

    (found,) = store.reviews()

    assert (found.version, found.id) == (2, "01a1569b119070abbe179a8da6ebf006")


def test_an_unreadable_review_names_its_file(store: ReviewStore) -> None:
    store.directory.mkdir(parents=True)
    broken = store.directory / "broken.json"
    broken.write_text('{"version": 2, "id": "broken"}')

    with pytest.raises(UnreadableReview, match=rf"^Can't read the review in {broken}: "):
        store.load("broken")
