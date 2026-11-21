from dataclasses import dataclass

from core.candidates import audiences_overlap, build_candidate, generate_candidates, pair_key


@dataclass
class FakeChunk:
    id: int
    text: str


class FakeAdapter:
    def __init__(self, neighbours: dict[int, list[tuple[int, float]]]):
        self.neighbours = neighbours

    def find_similar(self, chunk_id, k):
        return self.neighbours.get(chunk_id, [])[:k]


def test_exact_duplicate_ignores_case_and_spaces():
    c = build_candidate(FakeChunk(1, "Lock your laptop.  Always."), FakeChunk(2, "lock your laptop. always."), 0.99)
    assert c.exact_duplicate
    assert not c.numbers_differ


def test_different_numbers_are_flagged_not_duplicate():
    c = build_candidate(FakeChunk(1, "Refund within 7 days."), FakeChunk(2, "Refund within 3 days."), 0.97)
    assert not c.exact_duplicate
    assert c.numbers_differ
    assert c.priority == 2


def test_candidate_ids_are_ordered():
    c = build_candidate(FakeChunk(9, "a"), FakeChunk(4, "b"), 0.8)
    assert (c.a_id, c.b_id) == (4, 9)


def test_pair_key_is_order_independent():
    assert pair_key("hash1", "hash2") == pair_key("hash2", "hash1")


def test_generate_candidates_dedupes_filters_and_ranks():
    chunks = [
        FakeChunk(1, "Refund within 7 days."),
        FakeChunk(2, "Refund within 3 days."),
        FakeChunk(3, "Lunch is served at noon."),
    ]
    adapter = FakeAdapter({
        1: [(2, 0.95), (3, 0.30)],  # 1-3 is below the similarity threshold
        2: [(1, 0.95)],             # same pair as 1-2: must not appear twice
        3: [(1, 0.30)],
    })
    result = generate_candidates(adapter, chunks, top_k=5, min_similarity=0.5, max_pairs=10)
    assert [(c.a_id, c.b_id) for c in result] == [(1, 2)]


def test_generate_candidates_respects_max_pairs_and_priority():
    chunks = [FakeChunk(1, "Within 7 days."), FakeChunk(2, "Within 3 days."), FakeChunk(3, "Lock the door."), FakeChunk(4, "Lock the window.")]
    adapter = FakeAdapter({1: [(2, 0.80)], 3: [(4, 0.99)]})
    stats: dict = {}
    result = generate_candidates(adapter, chunks, top_k=5, min_similarity=0.5, max_pairs=1, stats=stats)
    # The pair with different numbers wins over the more similar pair without numbers.
    assert [(c.a_id, c.b_id) for c in result] == [(1, 2)]
    assert stats["pairs_found"] == 2 and stats["pairs_cut_by_cap"] == 1  # the cut is reported, not silent


def test_audiences_overlap():
    assert audiences_overlap([], ["Operations"])  # company-wide document: everyone sees it
    assert audiences_overlap(["Legal", "IT"], ["IT"])  # IT sees both
    assert not audiences_overlap(["Operations"], ["Finance"])  # nobody sees both


def test_pairs_nobody_can_see_together_are_skipped_and_counted():
    chunks = [FakeChunk(1, "Within 7 days."), FakeChunk(2, "Within 3 days.")]
    adapter = FakeAdapter({1: [(2, 0.9)]})
    stats: dict = {}
    result = generate_candidates(adapter, chunks, 5, 0.5, 10, allowed=lambda a, b: False, stats=stats)
    assert result == []
    assert stats["pairs_skipped_no_shared_audience"] == 1
