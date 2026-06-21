"""Regression coverage for MCP wrapper fixes from dogfooding."""

from __future__ import annotations

from just_prs.models import TraitInfo


class FakeRestClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def search_traits(self, term: str, limit: int = 25) -> list[TraitInfo]:
        self.calls.append(term)
        if term == "diabetes mellitus type 2":
            return []
        if term in {"diabetes mellitus, type 2", "diabetes"}:
            return [_trait()]
        return []

    def get_trait(self, efo_id: str) -> TraitInfo:
        return _trait(trait_id=efo_id)


class FakeCatalog:
    def percentile(
        self,
        prs_score: float,
        pgs_id: str,
        ancestry: str = "EUR",
        mean: float = 0.0,
        std: float | None = None,
        panel: str = "1000g",
    ) -> tuple[float | None, str]:
        return 0.0, "reference_panel"


def _trait(trait_id: str = "MONDO_0005148") -> TraitInfo:
    return TraitInfo(
        id=trait_id,
        label="type 2 diabetes mellitus",
        description="A type 2 diabetes trait.",
        url="https://example.test/trait",
        trait_categories=["Disease"],
        trait_synonyms=["diabetes mellitus, type 2"],
        associated_pgs_ids=["PGS000001", "PGS000002"],
        child_associated_pgs_ids=["PGS000003"],
    )


async def test_trait_info_accepts_trait_id_and_efo_id(essentials_client, monkeypatch):
    from just_prs_mcp import client as mcp_client

    monkeypatch.setattr(mcp_client, "make_rest_client", FakeRestClient)

    by_trait_id = await essentials_client.call_tool(
        "trait_info", {"trait_id": "MONDO_0005148"}
    )
    by_efo_id = await essentials_client.call_tool("trait_info", {"efo_id": "EFO_0001645"})

    assert by_trait_id.data.id == "MONDO_0005148"
    assert by_efo_id.data.id == "EFO_0001645"


async def test_search_traits_defaults_to_counts_and_retries_empty_query(
    essentials_client,
    monkeypatch,
):
    from just_prs_mcp import client as mcp_client

    monkeypatch.setattr(mcp_client, "make_rest_client", FakeRestClient)

    result = await essentials_client.call_tool(
        "search_traits", {"term": "diabetes mellitus type 2"}
    )

    assert result.data[0].id == "MONDO_0005148"
    assert result.data[0].n_associated == 2
    assert result.data[0].n_child_associated == 1
    assert not hasattr(result.data[0], "associated_pgs_ids")


async def test_search_traits_can_return_full_pgs_id_arrays(essentials_client, monkeypatch):
    from just_prs_mcp import client as mcp_client

    monkeypatch.setattr(mcp_client, "make_rest_client", FakeRestClient)

    result = await essentials_client.call_tool(
        "search_traits", {"term": "diabetes", "include_pgs_ids": True}
    )

    assert result.data[0].associated_pgs_ids == ["PGS000001", "PGS000002"]
    assert result.data[0].child_associated_pgs_ids == ["PGS000003"]


async def test_percentile_low_match_rate_is_unreliable(essentials_client, monkeypatch):
    from just_prs_mcp import client as mcp_client

    monkeypatch.setattr(mcp_client, "make_catalog", lambda settings: FakeCatalog())

    result = await essentials_client.call_tool(
        "percentile",
        {"prs_score": 14.944, "pgs_id": "PGS000014", "match_rate": 0.374},
    )

    assert result.data.percentile == 0.0
    assert result.data.reliable is False
    assert "Match rate" in result.data.caveat
