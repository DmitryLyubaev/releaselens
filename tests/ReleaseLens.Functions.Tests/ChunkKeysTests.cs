using System.Text;
using ReleaseLens.Functions.Common;

namespace ReleaseLens.Functions.Tests;

public class ChunkKeysTests
{
    [Fact]
    public void ChunkKeys_ForIsBase64UrlWithoutPaddingThenTheIndex()
    {
        var artefact = "release:python-1.44.1";

        var key = ChunkKeys.For(artefact, 3);

        var expected = Convert.ToBase64String(Encoding.UTF8.GetBytes(artefact))
            .TrimEnd('=').Replace('+', '-').Replace('/', '_');
        Assert.Equal($"{expected}-3", key);
        Assert.DoesNotContain('=', key);
        Assert.Matches("^[A-Za-z0-9_-]+$", key);
    }

    [Theory]
    [InlineData("issue:42")]
    [InlineData("commit:0123456789abcdef0123456789abcdef01234567")]
    [InlineData("release:v1.0+build/7?")]
    [InlineData("pull_request:é€\U0001F600")]
    public void ChunkKeys_ForOnlyUsesCharactersAnAiSearchKeyAllows(string artefact)
    {
        Assert.Matches("^[A-Za-z0-9_-]+$", ChunkKeys.For(artefact, 12));
    }

    [Fact]
    public void ChunkKeys_ForIsDeterministicAndDistinguishesIndexAndArtefact()
    {
        Assert.Equal(ChunkKeys.For("issue:1", 0), ChunkKeys.For("issue:1", 0));
        Assert.NotEqual(ChunkKeys.For("issue:1", 0), ChunkKeys.For("issue:1", 1));
        Assert.NotEqual(ChunkKeys.For("issue:1", 0), ChunkKeys.For("issue:2", 0));
    }

    [Fact]
    public void ChunkKeys_StaleIsEveryExistingKeyNotFresh()
    {
        var fresh = new HashSet<string> { ChunkKeys.For("issue:7", 0), ChunkKeys.For("issue:7", 1) };
        var existing = new[]
        {
            ChunkKeys.For("issue:7", 0),
            ChunkKeys.For("issue:7", 1),
            ChunkKeys.For("issue:7", 2),   // the artefact came back shorter
            "17",                          // a numeric key from the bulk-loaded corpus
        };

        var stale = ChunkKeys.Stale(existing, fresh);

        Assert.Equal([ChunkKeys.For("issue:7", 2), "17"], stale);
    }

    [Fact]
    public void ChunkKeys_StaleOfNothingStaleIsEmpty()
    {
        var fresh = new HashSet<string> { "a-0" };

        Assert.Empty(ChunkKeys.Stale(["a-0"], fresh));
        Assert.Empty(ChunkKeys.Stale([], fresh));
    }
}
