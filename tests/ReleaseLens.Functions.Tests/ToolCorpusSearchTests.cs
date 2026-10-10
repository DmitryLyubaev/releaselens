using ReleaseLens.Functions.Tool;

namespace ReleaseLens.Functions.Tests;

public class ToolCorpusSearchTests
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    [Theory]
    [InlineData(null, 5)]
    [InlineData(3, 3)]
    [InlineData(10, 10)]
    [InlineData(11, 10)]
    [InlineData(500, 10)]
    public async Task Search_DefaultsToFiveAndClampsTopToTen(int? top, int expected)
    {
        var stub = ToolFixtures.StubFor(("issue:1", "text"));

        await ToolFixtures.Search(stub).SearchAsync("how do I retry", top, Ct);

        var body = ToolFixtures.SearchBody(stub);
        Assert.Equal(expected, (int?)body["top"]);
        Assert.Equal(expected, (int?)body["vectorQueries"]![0]!["k"]);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-4)]
    [InlineData(int.MinValue)]
    public async Task Search_TopBelowOneBecomesOne(int top)
    {
        var stub = ToolFixtures.StubFor(("issue:1", "text"));

        await ToolFixtures.Search(stub).SearchAsync("how do I retry", top, Ct);

        Assert.Equal(1, (int?)ToolFixtures.SearchBody(stub)["top"]);
    }

    [Fact]
    public async Task Search_MapsTypeAndCutsTheExcerptAt500()
    {
        var stub = ToolFixtures.StubFor(
            ("issue:42", new string('a', 501)),
            ("release:v1.2.0", new string('b', 500)),
            ("discussion:7", "short"));

        var hits = await ToolFixtures.Search(stub).SearchAsync("importer", 5, Ct);

        Assert.Equal(["issue", "release", "discussion"], hits.Select(hit => hit.Type));
        // The artefact is the whole ID; only the type is the part before the first colon.
        Assert.Equal(["issue:42", "release:v1.2.0", "discussion:7"], hits.Select(hit => hit.Artefact));
        Assert.Equal(new string('a', 500), hits[0].Excerpt);
        Assert.Equal(new string('b', 500), hits[1].Excerpt);
        Assert.Equal("short", hits[2].Excerpt);
        Assert.Equal([3.0, 2.0, 1.0], hits.Select(hit => hit.Score));
    }

    [Fact]
    public async Task Search_NeverCutsAnExcerptInTheMiddleOfAPair()
    {
        // Character 500 is the first half of an emoji; keeping it would leave a lone surrogate.
        var stub = ToolFixtures.StubFor(("issue:1", new string('a', 499) + "\U0001F600" + "tail"));

        var hits = await ToolFixtures.Search(stub).SearchAsync("importer", 5, Ct);

        Assert.Equal(new string('a', 499), Assert.Single(hits).Excerpt);
    }

    [Fact]
    public async Task Search_AnArtefactWithNoColon_HasTheUnknownType()
    {
        var stub = ToolFixtures.StubFor(("odd-id", "text"));

        var hits = await ToolFixtures.Search(stub).SearchAsync("importer", 5, Ct);

        Assert.Equal("unknown", Assert.Single(hits).Type);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("\t\r\n")]
    public async Task Search_EmptyQuery_IsAToolError(string query)
    {
        var stub = new StubHttpHandler();

        var error = await Assert.ThrowsAsync<ToolInputException>(
            () => ToolFixtures.Search(stub).SearchAsync(query, null, Ct));

        Assert.Equal("query must not be empty", error.Message);
        // Nothing was embedded or searched.
        Assert.Empty(stub.Requests);
    }

    [Fact]
    public async Task Search_AVeryLongQuery_IsCutBeforeEmbedding()
    {
        var stub = ToolFixtures.StubFor(("issue:1", "text"));

        await ToolFixtures.Search(stub).SearchAsync(new string('q', 5000), null, Ct);

        var input = ToolFixtures.EmbeddingsBody(stub)["input"]!.AsArray();
        Assert.Equal(2000, Assert.Single(input)!.GetValue<string>().Length);
        // The keyword half of the hybrid search gets the same cut query.
        Assert.Equal(2000, ((string?)ToolFixtures.SearchBody(stub)["search"])!.Length);
    }

    [Fact]
    public async Task Search_EmbedsTheQueryAndSendsItToTheSearch()
    {
        var stub = ToolFixtures.StubFor(("issue:1", "text"));

        await ToolFixtures.Search(stub).SearchAsync("  how do I retry  ", null, Ct);

        Assert.Equal("how do I retry", ToolFixtures.EmbeddingsBody(stub)["input"]![0]!.GetValue<string>());
        Assert.Equal("how do I retry", (string?)ToolFixtures.SearchBody(stub)["search"]);
        Assert.Equal("semantic", (string?)ToolFixtures.SearchBody(stub)["queryType"]);
    }
}
