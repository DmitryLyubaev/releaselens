using System.Text.Json.Nodes;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Functions.Common;

namespace ReleaseLens.Functions.Tests;

public class ArtefactChunksTests
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    [Fact]
    public async Task Count_EqualsWhatIngestArtefactUpserts_ForTheRecordParsedBackFromItsJson()
    {
        // The exported file is the record as JSON; the ingest app parses it back, so the count is taken the same way.
        var parsed = ArtefactJson.Parse(ArtefactJson.Serialize(IngestFixtures.Issue()));
        var count = ArtefactChunks.Count(parsed);
        Assert.True(count > 1, "The fixture must make several chunks, or this test shows nothing.");
        var stub = IngestFixtures.StubFor(count);

        var result = await IngestFixtures.Ingest(stub).IngestAsync(parsed, Ct);

        Assert.Equal(count, result.Chunks);
        var upserted = Assert.Single(stub.Requests, r => r.Uri.AbsolutePath.EndsWith("/docs/index", StringComparison.Ordinal));
        Assert.Equal(count, JsonNode.Parse(upserted.Body)!["value"]!.AsArray().Count);
    }

    [Fact]
    public void Count_IsTheNumberOfChunksOf_UnderTheAppsOptions()
    {
        var issue = IngestFixtures.Issue();
        var count = ArtefactChunks.Count(issue);

        Assert.True(count > 1, "A single chunk would be the same under any options.");
        Assert.Equal(ArtefactChunks.Of(issue).Count, count);
        Assert.Equal(IngestFixtures.ChunksOf(issue).Count, count);
    }

    [Fact]
    public void Count_OfAShorterRecordIsSmaller()
    {
        Assert.True(ArtefactChunks.Count(IngestFixtures.Issue(sentences: 3)) < ArtefactChunks.Count(IngestFixtures.Issue()));
    }

    [Fact]
    public void Of_RefusesAnUnknownRecordType()
    {
        Assert.Throws<ArgumentException>(() => ArtefactChunks.Of(new NotARecord()));
    }

    [Fact]
    public void Manifest_MapsEachFileNameToTheCountOfItsOwnJson_SortedByName()
    {
        var long_ = ArtefactJson.Serialize(IngestFixtures.Issue());
        var short_ = ArtefactJson.Serialize(IngestFixtures.Issue(sentences: 3));

        var manifest = JsonNode.Parse(ArtefactChunks.Manifest(null, [("b.json", long_), ("a.json", short_)]))!.AsObject();

        Assert.Equal(["a.json", "b.json"], manifest.Select(pair => pair.Key));
        Assert.Equal(ArtefactChunks.Count(ArtefactJson.Parse(short_)), (int)manifest["a.json"]!);
        Assert.Equal(ArtefactChunks.Count(ArtefactJson.Parse(long_)), (int)manifest["b.json"]!);
    }

    [Fact]
    public void Manifest_KeepsEarlierEntriesAndReplacesAFileGivenAgain()
    {
        var one = ArtefactJson.Serialize(IngestFixtures.Issue(sentences: 3));
        var first = ArtefactChunks.Manifest(null, [("a.json", ArtefactJson.Serialize(IngestFixtures.Issue())), ("b.json", one)]);

        var second = JsonNode.Parse(ArtefactChunks.Manifest(first, [("a.json", one)]))!.AsObject();

        Assert.Equal(["a.json", "b.json"], second.Select(pair => pair.Key));
        Assert.Equal(ArtefactChunks.Count(ArtefactJson.Parse(one)), (int)second["a.json"]!);
    }

    [Fact]
    public void Manifest_RefusesAFileThatIsNotAnArtefactAndSameBytesForSameFiles()
    {
        Assert.Throws<InvalidArtefactException>(() => ArtefactChunks.Manifest(null, [("a.json", "{not json")]));
        var json = ArtefactJson.Serialize(IngestFixtures.Issue());
        Assert.Equal(ArtefactChunks.Manifest(null, [("a.json", json)]), ArtefactChunks.Manifest(null, [("a.json", json)]));
    }

    private sealed record NotARecord : IEvidenceRecord
    {
        public Guid TenantId => Guid.Empty;
        public EvidenceKey Key => new(EntityType.Issue, "1");
        public string Url => "";
    }
}
